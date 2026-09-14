"""API-key authentication."""
from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from app.config import API_KEY_HEADER_NAME, VALID_API_KEYS


def _is_valid(candidate: str) -> bool:
    """
    Constant-time membership test.

    A plain `in` on a set short-circuits on the first differing byte, which
    leaks key material through response timing. `compare_digest` against
    every configured key costs the same regardless of how much of a guess
    was correct.
    """
    matched = False
    for key in VALID_API_KEYS:
        if secrets.compare_digest(candidate, key):
            matched = True
    return matched


async def require_api_key(
    x_api_key: str | None = Header(None, alias=API_KEY_HEADER_NAME),
) -> str:
    """
    Validate the `x-api-key` header.

    401 when missing, 403 when present but wrong, so a client can tell "you
    forgot to send a key" from "your key is wrong or revoked".
    """
    if x_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing API key. Send it in the '{API_KEY_HEADER_NAME}' header.",
        )
    if not _is_valid(x_api_key):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key.")
    return x_api_key
