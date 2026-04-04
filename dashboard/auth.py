# dashboard/auth.py — Simple session-based login
import os
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeTimedSerializer

SECRET_KEY = os.getenv("DASHBOARD_SECRET", "change-me-in-production")
DASHBOARD_USER = os.getenv("DASHBOARD_USER", "admin")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "admin123")

_serializer = URLSafeTimedSerializer(SECRET_KEY)
COOKIE_NAME = "n2h_session"
MAX_AGE = 60 * 60 * 8  # 8 hours


def create_session_token(username: str) -> str:
    return _serializer.dumps(username)


def verify_session_token(token: str) -> str | None:
    try:
        return _serializer.loads(token, max_age=MAX_AGE)
    except Exception:
        return None


def get_current_user(request: Request) -> str | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return verify_session_token(token)


def require_login(request: Request) -> str:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/dashboard/login"})
    return user


def check_credentials(username: str, password: str) -> bool:
    return username == DASHBOARD_USER and password == DASHBOARD_PASSWORD
