/**
 * server.js — WA Bridge REST API
 *
 * Self-hosted WhatsApp gateway. Exposes a simple HTTP API so
 * your FastAPI bot can send messages without Meta Cloud API.
 *
 * ALL data stays on YOUR server:
 *   - Session files → ./sessions/
 *   - No third-party relay (Waha, Baileys-as-a-service, etc.)
 *   - Only connects to WhatsApp's own servers
 *
 * Security:
 *   - BRIDGE_API_KEY env var → every request must send X-Bridge-Key header
 *   - Only your FastAPI server should be able to reach this service
 *   - Run on internal network / localhost + reverse proxy
 *
 * Endpoints:
 *   POST   /sessions/:name/start      → start / create session
 *   GET    /sessions/:name/status     → get status
 *   GET    /sessions/:name/qr         → get raw QR string (for frontend to render)
 *   POST   /sessions/:name/send-text  → send a text message
 *   DELETE /sessions/:name            → logout + delete session files
 *   GET    /sessions                  → list all sessions
 *   GET    /health                    → health check (no auth needed)
 */

'use strict';

// ── Catch any unhandled crashes so Railway logs show the reason ───────────────
process.on('uncaughtException', (err) => {
    console.error('[WA-Bridge] UNCAUGHT EXCEPTION:', err.message);
    console.error(err.stack);
    process.exit(1);
});
process.on('unhandledRejection', (reason) => {
    console.error('[WA-Bridge] UNHANDLED REJECTION:', reason);
});

const express          = require('express');
const { SessionManager } = require('./session-manager');

const app     = express();
const manager = new SessionManager();

app.use(express.json());

// ── API key guard ─────────────────────────────────────────────────────────────
const BRIDGE_API_KEY = process.env.BRIDGE_API_KEY || '';

app.use((req, res, next) => {
    // Health check never needs auth
    if (req.path === '/health') return next();

    if (BRIDGE_API_KEY && req.headers['x-bridge-key'] !== BRIDGE_API_KEY) {
        return res.status(401).json({ error: 'Unauthorized — wrong or missing X-Bridge-Key' });
    }
    next();
});

// ── Health check ──────────────────────────────────────────────────────────────
app.get('/health', (_req, res) => {
    res.json({
        status: 'ok',
        sessions: manager.listSessions().length,
        timestamp: new Date().toISOString(),
    });
});

// ── List all sessions ─────────────────────────────────────────────────────────
app.get('/sessions', (_req, res) => {
    res.json(manager.listSessions());
});

// ── Start / create session ────────────────────────────────────────────────────
app.post('/sessions/:name/start', async (req, res) => {
    const { name } = req.params;

    if (!name || !/^[\w-]+$/.test(name)) {
        return res.status(400).json({ error: 'Invalid session name (alphanumeric + hyphens only)' });
    }

    try {
        const result = await manager.startSession(name);
        res.json(result);
    } catch (err) {
        console.error(`[Server] Start session error: ${err.message}`);
        res.status(500).json({ error: err.message });
    }
});

// ── Get status ────────────────────────────────────────────────────────────────
app.get('/sessions/:name/status', (req, res) => {
    const status = manager.getStatus(req.params.name);
    res.json({ session: req.params.name, status });
});

// ── Get QR code ───────────────────────────────────────────────────────────────
// Returns the raw QR string — your CMS renders it via qrcode.js in the browser
// Keeps the QR off any external image server
app.get('/sessions/:name/qr', (req, res) => {
    const qr = manager.getQR(req.params.name);

    if (!qr) {
        const status = manager.getStatus(req.params.name);
        return res.status(404).json({
            error: 'No QR available',
            status,
            hint: status === 'CONNECTED'
                ? 'Session already connected'
                : status === 'NOT_STARTED'
                    ? 'Call POST /sessions/:name/start first'
                    : 'QR not generated yet — try again in 2s',
        });
    }

    res.json({ session: req.params.name, qr });  // raw QR string, render client-side
});

// ── Send text message ─────────────────────────────────────────────────────────
app.post('/sessions/:name/send-text', async (req, res) => {
    const { name } = req.params;
    const { to, message } = req.body;

    if (!to || !message) {
        return res.status(400).json({ error: '"to" and "message" are required' });
    }

    try {
        await manager.sendText(name, String(to), String(message));
        res.json({ success: true });
    } catch (err) {
        console.error(`[Server] Send error (${name}): ${err.message}`);
        res.status(500).json({ error: err.message });
    }
});

// ── Delete / logout session ───────────────────────────────────────────────────
app.delete('/sessions/:name', async (req, res) => {
    try {
        await manager.deleteSession(req.params.name);
        res.json({ success: true, message: `Session "${req.params.name}" deleted` });
    } catch (err) {
        console.error(`[Server] Delete error: ${err.message}`);
        res.status(500).json({ error: err.message });
    }
});

// ── 404 fallback ──────────────────────────────────────────────────────────────
app.use((_req, res) => {
    res.status(404).json({ error: 'Endpoint not found' });
});

// ── Start server ──────────────────────────────────────────────────────────────
const PORT = parseInt(process.env.BRIDGE_PORT || '3000', 10);
app.listen(PORT, '0.0.0.0', () => {
    console.log(`[WA-Bridge] Running on port ${PORT}`);
    console.log(`[WA-Bridge] API key protection: ${BRIDGE_API_KEY ? 'ENABLED' : 'DISABLED (set BRIDGE_API_KEY)'}`);
    console.log(`[WA-Bridge] FastAPI webhook: ${process.env.FASTAPI_WEBHOOK_URL || 'NOT SET'}`);
    console.log(`[WA-Bridge] Sessions dir: ${process.env.SESSIONS_DIR || './sessions'}`);
});
