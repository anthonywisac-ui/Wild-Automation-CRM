# main.py for restaurant bot
import os
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, HTMLResponse
import uvicorn
import stripe
from .config import VERIFY_TOKEN, STRIPE_SECRET_KEY, MANAGER_NUMBER
from .flow import handle_flow, get_session, new_session
from .whatsapp_handlers import send_language_selection, send_text_message
from .stripe_utils import handle_stripe_webhook
from .menu_data import load_menu
from .strings import load_strings
from session import SharedSession
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from db import authenticate_user, create_access_token, get_user, decode_token, create_user

security = HTTPBearer()

stripe.api_key = STRIPE_SECRET_KEY

app = FastAPI()

@app.on_event("startup")
async def startup():
    load_menu()
    load_strings()

@app.on_event("shutdown")
async def shutdown():
    await SharedSession.close_session()

@app.get("/webhook")
async def verify_webhook(request: Request):
    params = dict(request.query_params)
    if params.get("hub.verify_token") == VERIFY_TOKEN:
        return PlainTextResponse(params.get("hub.challenge", ""))
    return PlainTextResponse("Forbidden", status_code=403)

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    try:
        entry = data["entry"][0]["changes"][0]["value"]
        if "messages" in entry:
            message = entry["messages"][0]
            sender = message["from"]
            if sender == MANAGER_NUMBER:
                return {"status": "ok"}
            msg_type = message.get("type", "")
            if msg_type == "text":
                text = message["text"]["body"].strip()
                await handle_flow(sender, text)
            elif msg_type == "interactive":
                interactive = message["interactive"]
                if interactive["type"] == "button_reply":
                    btn_id = interactive["button_reply"]["id"]
                    await handle_flow(sender, btn_id, is_button=True)
                elif interactive["type"] == "list_reply":
                    list_id = interactive["list_reply"]["id"]
                    await handle_flow(sender, list_id, is_button=True)
    except Exception as e:
        print(f"Webhook error: {e}")
    return {"status": "ok"}

@app.post("/stripe-webhook")
async def stripe_webhook_endpoint(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    return await handle_stripe_webhook(payload, sig_header)

@app.get("/success")
async def payment_success():
    return HTMLResponse("<h1>Payment successful!</h1>")

@app.get("/cancel")
async def payment_cancel():
    return HTMLResponse("<h1>Payment cancelled</h1>")

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    username = payload.get("sub")
    user = get_user(username)
    if not user:
        raise HTTPException(status_code=401)
    return user

@app.post("/auth/login")
def login(username: str, password: str):
    user = authenticate_user(username, password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": username})
    return {"access_token": token, "token_type": "bearer"}

@app.post("/auth/register")
def register(username: str, password: str):
    user = create_user(username, password, role="user")
    if not user:
        raise HTTPException(status_code=400, detail="Username exists")
    return {"msg": "User created"}

@app.get("/auth/me")
def me(current_user: dict = Depends(get_current_user)):
    return {"username": current_user.get("username"), "role": current_user.get("role", "user"), "user_id": current_user.get("user_id")}

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
