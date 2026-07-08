"""
Simple bearer token authentication for the API.

Set API_TOKEN in environment or .env file. If not set, auth is disabled.
"""

import os

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)
_token = os.getenv("API_TOKEN", "")


def _get_token():
    """Read token lazily so .env has time to load."""
    global _token
    _token = os.getenv("API_TOKEN", "")
    return _token


async def require_auth(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
):
    """FastAPI dependency — checks Authorization: Bearer <token>.

    Skips auth when API_TOKEN is not set (development mode).
    """
    token = _get_token()
    if not token:
        return  # auth disabled

    # Skip auth for docs and OpenAPI schema
    if request.url.path in ("/docs", "/redoc", "/openapi.json"):
        return

    if credentials is None or credentials.credentials != token:
        raise HTTPException(status_code=401, detail="Invalid or missing token")
