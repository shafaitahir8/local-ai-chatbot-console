"""
Authentication utilities and dependencies.
"""

import os
import hashlib
import hmac
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials, HTTPBearer, HTTPAuthorizationCredentials
import jwt
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ..models.database import AdminUser, RateLimitEvent, get_db
from ..services.redis_service import redis_service
from ..services.runtime_secret_service import runtime_secret_service

# Security
security_basic = HTTPBasic(auto_error=False)
security_bearer = HTTPBearer(auto_error=False)

# JWT Configuration
SECRET_KEY = runtime_secret_service.get_or_create_secret("JWT_SECRET", "jwt_secret")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

# Default admin credentials (should be changed in production)
DEFAULT_ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
_token_cache_lock = threading.Lock()
_token_cache: dict[str, dict] = {}


def hash_password(password: str) -> str:
    """Hash a password using SHA-256."""
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a password against its hash."""
    return hash_password(plain) == hashed


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """Decode and verify a JWT token."""
    now = time.time()
    with _token_cache_lock:
        cached = _token_cache.get(token)
        if cached and float(cached.get("expires_at", 0.0)) > now:
            return dict(cached.get("payload") or {})
        if cached:
            _token_cache.pop(token, None)
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        expires_at = float(payload.get("exp", 0) or 0)
        if expires_at > now:
            with _token_cache_lock:
                _token_cache[token] = {
                    "payload": dict(payload),
                    "expires_at": expires_at,
                }
        return payload
    except jwt.PyJWTError:
        return None


def ensure_default_admin(db: Session):
    """Create the bootstrap admin account if it does not exist.

    This must never overwrite an existing password hash. Once the admin rotates
    the credential, the stored password becomes the source of truth.
    """
    admin = db.query(AdminUser).filter(AdminUser.username == DEFAULT_ADMIN_USERNAME).first()
    expected_hash = hash_password(DEFAULT_ADMIN_PASSWORD)
    
    if not admin:
        admin = AdminUser(
            username=DEFAULT_ADMIN_USERNAME,
            password_hash=expected_hash
        )
        db.add(admin)
        db.commit()


async def get_current_user(
    request: Request,
    basic_credentials: HTTPBasicCredentials = Depends(security_basic),
    bearer_credentials: HTTPAuthorizationCredentials = Depends(security_bearer),
    db: Session = Depends(get_db)
) -> str:
    """
    Authenticate user via Basic Auth or Bearer Token.
    
    Returns:
        Username of authenticated user
    """
    # Ensure default admin exists
    ensure_default_admin(db)
    
    # Try Bearer token first
    if bearer_credentials:
        payload = decode_token(bearer_credentials.credentials)
        if payload and "sub" in payload:
            return payload["sub"]
    
    # Try Basic Auth
    if basic_credentials:
        user = db.query(AdminUser).filter(
            AdminUser.username == basic_credentials.username
        ).first()
        
        if user and verify_password(basic_credentials.password, user.password_hash):
            # Update last login
            user.last_login = datetime.utcnow()
            db.commit()
            return user.username
    
    # Check for session cookie
    session_token = request.cookies.get("session_token")
    if session_token:
        payload = decode_token(session_token)
        if payload and "sub" in payload:
            return payload["sub"]
    
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"}
    )


async def optional_auth(
    request: Request,
    bearer_credentials: HTTPAuthorizationCredentials = Depends(security_bearer),
) -> Optional[str]:
    """
    Optional authentication - returns None if not authenticated.
    """
    if bearer_credentials:
        payload = decode_token(bearer_credentials.credentials)
        if payload and "sub" in payload:
            return payload["sub"]
    
    session_token = request.cookies.get("session_token")
    if session_token:
        payload = decode_token(session_token)
        if payload and "sub" in payload:
            return payload["sub"]
    
    return None


RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "60"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))  # seconds
RATE_LIMIT_USE_DB_FALLBACK = os.getenv("RATE_LIMIT_USE_DB_FALLBACK", "false").strip().lower() in {"1", "true", "yes"}
PUBLIC_QUERY_TOKEN_COMPAT = os.getenv("PUBLIC_QUERY_TOKEN_COMPAT", "true").strip().lower() in {"1", "true", "yes"}
PUBLIC_JSON_BODY_LIMIT_BYTES = int(os.getenv("PUBLIC_JSON_BODY_LIMIT_BYTES", str(64 * 1024)))


class _InMemoryRateLimiter:
    """Fast process-local sliding-window limiter for Redis-offline fallback."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, deque[float]] = {}
        self._last_cleanup = 0.0

    def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        window_start = now - max(1, int(window_seconds))

        with self._lock:
            bucket = self._events.get(key)
            if bucket is None:
                bucket = deque()
                self._events[key] = bucket

            while bucket and bucket[0] < window_start:
                bucket.popleft()

            if len(bucket) >= limit:
                return False

            bucket.append(now)

            if now - self._last_cleanup >= max(5.0, float(window_seconds)):
                self._cleanup_locked(window_start)
                self._last_cleanup = now

            return True

    def _cleanup_locked(self, window_start: float) -> None:
        stale_keys = []
        for key, bucket in self._events.items():
            while bucket and bucket[0] < window_start:
                bucket.popleft()
            if not bucket:
                stale_keys.append(key)
        for key in stale_keys:
            self._events.pop(key, None)


_in_memory_rate_limiter = _InMemoryRateLimiter()


def _public_shared_secret() -> str:
    return os.getenv("TUNNEL_SHARED_SECRET", "").strip()


def _get_query_token(request: Request) -> Optional[str]:
    if not PUBLIC_QUERY_TOKEN_COMPAT:
        return None
    raw = request.query_params.get("token")
    if raw is None:
        return None
    return str(raw)


def get_public_token_candidate(request: Request) -> tuple[Optional[str], Optional[str]]:
    auth_header = request.headers.get("Authorization")
    if auth_header is not None:
        auth_value = auth_header.strip()
        if not auth_value:
            return None, "Missing bearer token"
        scheme, _, credentials = auth_value.partition(" ")
        if scheme.lower() != "bearer":
            return None, "Malformed authorization header"
        if not credentials.strip():
            return None, "Missing bearer token"
        return credentials.strip(), "authorization"

    tunnel_token = request.headers.get("X-Tunnel-Token")
    if tunnel_token is not None:
        tunnel_value = str(tunnel_token).strip()
        if not tunnel_value:
            return None, "Missing tunnel token"
        return tunnel_value, "x-tunnel-token"

    query_token = _get_query_token(request)
    if query_token is not None:
        query_value = str(query_token).strip()
        if not query_value:
            return None, "Missing query token"
        return query_value, "query"

    return None, None


def get_public_token_fingerprint(request: Request) -> Optional[str]:
    candidate, source_or_error = get_public_token_candidate(request)
    if not candidate or source_or_error not in {"authorization", "x-tunnel-token", "query"}:
        return None
    return hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:16]


async def enforce_public_bot_token(request: Request) -> Optional[str]:
    required = _public_shared_secret()
    if not required:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Public bot access is not configured",
        )

    candidate, source_or_error = get_public_token_candidate(request)
    if source_or_error and source_or_error not in {"authorization", "x-tunnel-token", "query"}:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=source_or_error,
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not candidate:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing public access token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not hmac.compare_digest(candidate, required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid public access token",
        )
    return candidate


async def enforce_public_json_request(request: Request) -> None:
    if request.method.upper() not in {"POST", "PUT", "PATCH"}:
        return
    content_type = str(request.headers.get("content-type") or "").lower()
    if "application/json" not in content_type:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Content-Type must be application/json",
        )
    raw_length = str(request.headers.get("content-length") or "").strip()
    if raw_length:
        try:
            if int(raw_length) > PUBLIC_JSON_BODY_LIMIT_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Request body too large",
                )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Content-Length header",
            )


def check_rate_limit(db: Session, client_ip: str, bot_id: str = None) -> bool:
    """
    Check if request is within rate limits.
    
    Args:
        client_ip: Client IP address
        bot_id: Optional bot ID for per-bot limits
        
    Returns:
        True if within limits
    """
    key = f"{client_ip}:{bot_id}" if bot_id else client_ip
    now = datetime.utcnow()
    window_start = now - timedelta(seconds=RATE_LIMIT_WINDOW)

    redis_allowed = redis_service.check_rate_limit(
        key,
        limit=RATE_LIMIT_REQUESTS,
        window_seconds=RATE_LIMIT_WINDOW,
    )
    if redis_allowed is not None:
        return bool(redis_allowed)

    if not RATE_LIMIT_USE_DB_FALLBACK:
        return _in_memory_rate_limiter.allow(
            key,
            limit=RATE_LIMIT_REQUESTS,
            window_seconds=RATE_LIMIT_WINDOW,
        )

    db.query(RateLimitEvent).filter(RateLimitEvent.created_at < window_start).delete()

    request_count = db.query(RateLimitEvent).filter(
        RateLimitEvent.key == key,
        RateLimitEvent.created_at >= window_start,
    ).count()
    if request_count >= RATE_LIMIT_REQUESTS:
        db.commit()
        return False

    db.add(RateLimitEvent(key=key, created_at=now))
    db.commit()
    return True


async def rate_limit_dependency(request: Request, bot_id: str = None, db: Session | None = None):
    """FastAPI dependency for rate limiting."""
    client_ip = request.client.host if request.client else "unknown"

    if db is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Rate limiter database session is required",
        )

    if not check_rate_limit(db, client_ip, bot_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please try again later."
        )


async def check_rate_limit_async(
    db: AsyncSession,
    client_ip: str,
    bot_id: str = None,
    token_fingerprint: str | None = None,
) -> bool:
    """Async rate limit check for public request-serving paths."""
    key_parts = [client_ip]
    if bot_id:
        key_parts.append(bot_id)
    if token_fingerprint:
        key_parts.append(token_fingerprint)
    key = ":".join(key_parts)
    now = datetime.utcnow()
    window_start = now - timedelta(seconds=RATE_LIMIT_WINDOW)

    redis_allowed = redis_service.check_rate_limit(
        key,
        limit=RATE_LIMIT_REQUESTS,
        window_seconds=RATE_LIMIT_WINDOW,
    )
    if redis_allowed is not None:
        return bool(redis_allowed)

    if not RATE_LIMIT_USE_DB_FALLBACK:
        return _in_memory_rate_limiter.allow(
            key,
            limit=RATE_LIMIT_REQUESTS,
            window_seconds=RATE_LIMIT_WINDOW,
        )

    await db.execute(delete(RateLimitEvent).where(RateLimitEvent.created_at < window_start))
    count_result = await db.execute(
        select(func.count()).select_from(RateLimitEvent).where(
            RateLimitEvent.key == key,
            RateLimitEvent.created_at >= window_start,
        )
    )
    request_count = int(count_result.scalar() or 0)
    if request_count >= RATE_LIMIT_REQUESTS:
        await db.flush()
        return False

    db.add(RateLimitEvent(key=key, created_at=now))
    await db.flush()
    return True


async def rate_limit_dependency_async(request: Request, bot_id: str, db: AsyncSession) -> None:
    client_ip = request.client.host if request.client else "unknown"
    token_fingerprint = get_public_token_fingerprint(request)
    if not await check_rate_limit_async(db, client_ip, bot_id, token_fingerprint=token_fingerprint):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please try again later."
        )
