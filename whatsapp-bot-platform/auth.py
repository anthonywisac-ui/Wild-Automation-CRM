"""Authentication module - single implementation (FIX #4)"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from db import (
    get_db, get_user_by_username, decode_token,
    hash_password, verify_password, create_access_token
)

security = HTTPBearer()

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    """✅ FIXED: Single implementation (no duplicates)"""
    token = credentials.credentials
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="No username in token")
    user = get_user_by_username(db, username)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if user.is_suspended:
        raise HTTPException(status_code=403, detail="Account suspended")
    return user

# ========== Export for use in other modules ==========
get_password_hash = hash_password

__all__ = ["get_current_user", "get_password_hash", "create_access_token", "security", "decode_token", "verify_password"]
