"""
Authentication utilities and dependencies.
"""

import os
import hashlib
from datetime import datetime, timedelta
from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials, HTTPBearer, HTTPAuthorizationCredentials
import secrets
import jwt
from sqlalchemy.orm import Session

from ..models.database import AdminUser, get_db

# Security
security_basic = HTTPBasic(auto_error=False)
security_bearer = HTTPBearer(auto_error=False)

# JWT Configuration
SECRET_KEY = os.getenv("JWT_SECRET", secrets.token_urlsafe(32))
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

# Default admin credentials (should be changed in production)
DEFAULT_ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
DEFAULT_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")


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
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.PyJWTError:
        return None


def ensure_default_admin(db: Session):
    """Create default admin user if none exists, or update password if changed."""
    admin = db.query(AdminUser).filter(AdminUser.username == DEFAULT_ADMIN_USERNAME).first()
    expected_hash = hash_password(DEFAULT_ADMIN_PASSWORD)
    
    if not admin:
        # Create new admin
        admin = AdminUser(
            username=DEFAULT_ADMIN_USERNAME,
            password_hash=expected_hash
        )
        db.add(admin)
        db.commit()
    elif admin.password_hash != expected_hash:
        # Update password if .env changed
        admin.password_hash = expected_hash
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


# Rate limiting
from collections import defaultdict
import time

rate_limit_data = defaultdict(list)
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "60"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))  # seconds


def check_rate_limit(client_ip: str, bot_id: str = None) -> bool:
    """
    Check if request is within rate limits.
    
    Args:
        client_ip: Client IP address
        bot_id: Optional bot ID for per-bot limits
        
    Returns:
        True if within limits
    """
    key = f"{client_ip}:{bot_id}" if bot_id else client_ip
    now = time.time()
    
    # Clean old entries
    rate_limit_data[key] = [t for t in rate_limit_data[key] if now - t < RATE_LIMIT_WINDOW]
    
    # Check limit
    if len(rate_limit_data[key]) >= RATE_LIMIT_REQUESTS:
        return False
    
    rate_limit_data[key].append(now)
    return True


async def rate_limit_dependency(request: Request, bot_id: str = None):
    """FastAPI dependency for rate limiting."""
    client_ip = request.client.host if request.client else "unknown"
    
    if not check_rate_limit(client_ip, bot_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please try again later."
        )
