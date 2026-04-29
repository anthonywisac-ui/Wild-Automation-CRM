/**
 * session-manager.js
 *
 * Manages multiple whatsapp-web.js sessions (one per customer bot).
 * - All session data stored locally in ./sessions/ folder (YOUR server only)
 * - Auto-reconnect on disconnect with exponential backoff
 * - Fixes known wwebjs issues: frame detachment, session restore
 * - Forwards incoming messages to FastAPI via HTTP POST
 * - ZERO external services — only connects to WhatsApp servers
 */

'use strict';

// whatsapp-web.js loaded lazily — only when first session is created.
// This lets the Express server start even if Chromium is not yet ready.
let _wwebjs = null;
function getWwebjs() {
    if (!_wwebjs) _wwebjs = require('whatsapp-web.js');
    return _wwebjs;
}

const axios = require('axios');
const path  = require('path');
const fs    = require('fs');

// ── Config from env ──────────────────────────────────────────────────────────
const SESSIONS_DIR       = process.env.SESSIONS_DIR      || path.join(__dirname, 'sessions');
const FASTAPI_WEBHOOK    = process.env.FASTAPI_WEBHOOK_URL
                           || `http://localhost:${process.env.PORT || 8000}/wwebjs/webhook`;
const BRIDGE_SECRET      = process.env.BRIDGE_INTERNAL_SECRET || '';
const MAX_RECONNECT_MS   = 5 * 60 * 1000;  // cap reconnect delay at 5 min
const WWEBJS_CACHE_PATH  = path.join(SESSIONS_DIR, '.wwebjs_cache');

// Ensure dirs exist — wrapped in try/catch so a bad volume mount won't crash the server
[SESSIONS_DIR, WWEBJS_CACHE_PATH].forEach(d => {
    try {
        if (!fs.existsSync(d)) fs.mkdirSync(d, { recursive: true });
    } catch (err) {
        console.error(`[WA-Bridge] WARNING: could not create dir ${d}: ${err.message}`);
    }
});

// Puppeteer args that work inside Docker / low-memory VPS
const PUPPETEER_ARGS = [
    '--no-sandbox',
    '--disable-setuid-sandbox',
    '--disable-dev-shm-usage',
    '--disable-accelerated-2d-canvas',
    '--no-first-run',
    '--no-zygote',
    '--disable-gpu',
    '--disable-extensions',
];

class SessionManager {
    constructor() {
        /** @type {Map<string, import('whatsapp-web.js').Client>} */
        this._clients   = new Map();
        /** @type {Map<string, string>}  name → raw QR string */
        this._qrCodes   = new Map();
        /** @type {Map<string, string>}  name → status string */
        this._statuses  = new Map();
        /** @type {Map<string, ReturnType<typeof setTimeout>>} */
        this._reconnectTimers = new Map();

        this._restorePersistedSessions();
    }

    // ── Public API ────────────────────────────────────────────────────────────

    /**
     * Start (or restart) a named session.
     * @param {string} name - Unique session name e.g. "bot_7"
     * @returns {Promise<{status: string, session: string}>}
     */
    async startSession(name) {
        // If already connected, return early
        if (this._statuses.get(name) === 'CONNECTED') {
            return { status: 'already_connected', session: name };
        }

        // Destroy stale client if one exists
        await this._destroyClient(name, false);

        this._statuses.set(name, 'STARTING');
        this._qrCodes.delete(name);

        const { Client, LocalAuth } = getWwebjs();
        const client = new Client({
            authStrategy: new LocalAuth({
                clientId: name,
                dataPath: SESSIONS_DIR,
            }),
            puppeteer: {
                headless: true,
                executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || '/usr/bin/chromium',
                args: PUPPETEER_ARGS,
            },
            webVersionCache: {
                type: 'remote',
                remotePath: 'https://raw.githubusercontent.com/wppconnect-team/wa-version/main/html/',
            },
        });

        this._clients.set(name, client);
        this._attachEvents(name, client);

        // initialize() is non-blocking for the QR phase;
        // catch hard failures (Puppeteer crash, browser not found)
        client.initialize().catch(err => {
            console.error(`[WA-Bridge] initialize() crash for "${name}": ${err.message}`);
            this._statuses.set(name, 'ERROR');
            this._clients.delete(name);

            // Puppeteer / frame errors → retry
            const isRecoverable = ['Target', 'frame', 'Session', 'Protocol', 'detach']
                .some(k => err.message.includes(k));
            if (isRecoverable) this._scheduleReconnect(name, 8000);
        });

        return { status: this._statuses.get(name), session: name };
    }

    /** Get raw QR string (null if not in QR phase) */
    getQR(name) {
        return this._qrCodes.get(name) || null;
    }

    /** Get current session status */
    getStatus(name) {
        return this._statuses.get(name) || 'NOT_STARTED';
    }

    /** List all tracked sessions */
    listSessions() {
        const result = [];
        for (const [name, status] of this._statuses) {
            result.push({ name, status });
        }
        return result;
    }

    /**
     * Send a plain-text message.
     * @param {string} name    - Session name
     * @param {string} to      - Recipient phone e.g. "923001234567"
     * @param {string} message - Message body
     */
    async sendText(name, to, message) {
        const client = this._clients.get(name);
        if (!client) {
            throw new Error(`Session "${name}" not found`);
        }
        if (this._statuses.get(name) !== 'CONNECTED') {
            throw new Error(`Session "${name}" not connected (status: ${this._statuses.get(name)})`);
        }

        const chatId = this._toChatId(to);
        await client.sendMessage(chatId, message);
    }

    /**
     * Logout and delete a session (removes local auth files).
     * @param {string} name
     */
    async deleteSession(name) {
        // Cancel reconnect timer
        if (this._reconnectTimers.has(name)) {
            clearTimeout(this._reconnectTimers.get(name));
            this._reconnectTimers.delete(name);
        }

        await this._destroyClient(name, true);   // true = also delete local auth files

        this._statuses.delete(name);
        this._qrCodes.delete(name);
    }

    // ── Private helpers ───────────────────────────────────────────────────────

    _attachEvents(name, client) {
        // QR generated — store for CMS to display
        client.on('qr', qr => {
            console.log(`[WA-Bridge] QR ready for "${name}"`);
            this._qrCodes.set(name, qr);
            this._statuses.set(name, 'SCAN_QR_CODE');
        });

        // Auth token saved (session persisted)
        client.on('authenticated', () => {
            console.log(`[WA-Bridge] Authenticated: "${name}"`);
            this._statuses.set(name, 'AUTHENTICATED');
            this._qrCodes.delete(name);
        });

        // Fully ready — can send/receive
        client.on('ready', () => {
            console.log(`[WA-Bridge] Ready: "${name}"`);
            this._statuses.set(name, 'CONNECTED');
            this._qrCodes.delete(name);

            // Cancel any pending reconnect
            if (this._reconnectTimers.has(name)) {
                clearTimeout(this._reconnectTimers.get(name));
                this._reconnectTimers.delete(name);
            }
        });

        // Incoming message — forward to FastAPI
        client.on('message', async msg => {
            // Skip: messages sent by us, group messages, non-text
            if (msg.fromMe)                    return;
            if (msg.from.includes('@g.us'))    return;
            if (msg.type !== 'chat')           return;

            const from = msg.from.replace('@c.us', '');
            console.log(`[WA-Bridge] MSG from ${from} on session "${name}": ${msg.body.substring(0, 60)}`);

            try {
                await this._forwardToFastAPI(name, from, msg.body);
            } catch (err) {
                console.error(`[WA-Bridge] Forward failed (${name}): ${err.message}`);
            }
        });

        // Auth failure — do NOT reconnect (session corrupted or banned)
        client.on('auth_failure', reason => {
            console.error(`[WA-Bridge] Auth failure for "${name}": ${reason}`);
            this._statuses.set(name, 'AUTH_FAILURE');
            this._clients.delete(name);
        });

        // Disconnected — auto-reconnect unless user explicitly logged out
        client.on('disconnected', reason => {
            console.warn(`[WA-Bridge] Disconnected "${name}" reason: ${reason}`);
            this._statuses.set(name, 'DISCONNECTED');
            this._clients.delete(name);

            if (reason !== 'LOGOUT') {
                this._scheduleReconnect(name, 10000);
            }
        });
    }

    _scheduleReconnect(name, delayMs) {
        if (this._reconnectTimers.has(name)) return;   // Already queued

        const capped = Math.min(delayMs, MAX_RECONNECT_MS);
        console.log(`[WA-Bridge] Reconnecting "${name}" in ${capped / 1000}s`);

        const timer = setTimeout(async () => {
            this._reconnectTimers.delete(name);
            try {
                await this.startSession(name);
            } catch (err) {
                console.error(`[WA-Bridge] Reconnect error for "${name}": ${err.message}`);
                // Exponential backoff: double the delay next round
                this._scheduleReconnect(name, Math.min(delayMs * 2, MAX_RECONNECT_MS));
            }
        }, capped);

        this._reconnectTimers.set(name, timer);
    }

    async _destroyClient(name, deleteFiles) {
        const client = this._clients.get(name);
        if (!client) return;

        try { await client.logout(); }  catch (_) { /* ignore */ }
        try { await client.destroy(); } catch (_) { /* ignore */ }
        this._clients.delete(name);

        if (deleteFiles) {
            // LocalAuth stores files at SESSIONS_DIR/session-<name>/
            const sessionPath = path.join(SESSIONS_DIR, `session-${name}`);
            if (fs.existsSync(sessionPath)) {
                fs.rmSync(sessionPath, { recursive: true, force: true });
                console.log(`[WA-Bridge] Deleted session files for "${name}"`);
            }
        }
    }

    async _forwardToFastAPI(session, from, body) {
        if (!FASTAPI_WEBHOOK) {
            console.warn('[WA-Bridge] FASTAPI_WEBHOOK_URL not set — message dropped');
            return;
        }

        const payload = {
            session,
            from,
            body,
            type: 'text',
            timestamp: Math.floor(Date.now() / 1000),
        };

        const headers = { 'Content-Type': 'application/json' };
        if (BRIDGE_SECRET) headers['X-Bridge-Secret'] = BRIDGE_SECRET;

        await axios.post(FASTAPI_WEBHOOK, payload, {
            headers,
            timeout: 15000,
        });
    }

    _restorePersistedSessions() {
        try {
            const entries = fs.readdirSync(SESSIONS_DIR, { withFileTypes: true });
            for (const entry of entries) {
                // LocalAuth creates "session-<name>" directories
                if (entry.isDirectory() && entry.name.startsWith('session-')) {
                    const name = entry.name.replace('session-', '');
                    if (!name || name === '.wwebjs_cache') continue;
                    console.log(`[WA-Bridge] Restoring persisted session: "${name}"`);
                    this.startSession(name).catch(e =>
                        console.error(`[WA-Bridge] Restore failed for "${name}": ${e.message}`)
                    );
                }
            }
        } catch (_) {
            console.log('[WA-Bridge] No persisted sessions found');
        }
    }

    /** Convert plain phone number to WhatsApp chat ID */
    _toChatId(phone) {
        const digits = phone.replace(/\D/g, '');
        return `${digits}@c.us`;
    }
}

module.exports = { SessionManager };
