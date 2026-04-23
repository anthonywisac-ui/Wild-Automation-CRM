import logging
from fastapi import FastAPI, Request
from .flow import handle_flow

app = FastAPI()
logger = logging.getLogger(__name__)

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    # This sub-app is called by the main platform router
    # but can also be used standalone if needed
    return {"status": "ok"}
