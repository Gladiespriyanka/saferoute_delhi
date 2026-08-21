"""API-key authentication for the SafeRoute Delhi API."""
from __future__ import annotations

from fastapi import Header, HTTPException, status

from app.config import API_KEY_HEADER_NAME, VALID_API_KEYS


async def require_api_key(x_api_key: str | None = Header(None, alias=API_KEY_HEADER_NAME)) -> str:
    """
    FastAPI dependency that validates the `x-api-key` header.

    Raises 401 if missing, 403 if present but invalid, so clients can
    distinguish "you forgot to send a key" from "your key is wrong/revoked".
    """
    if x_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing API key. Send it in the '{API_KEY_HEADER_NAME}' header.",
        )
    if x_api_key not in VALID_API_KEYS:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key.")
    return x_api_key
