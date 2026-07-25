from datetime import datetime, timedelta, timezone

import jwt


TOKEN_LIFETIME_MINUTES = 30
JWT_ALGORITHM = "HS256"


def create_token(username: str, secret: str) -> str:
    now = datetime.now(timezone.utc)
    claims = {"sub": username, "iat": now, "exp": now + timedelta(minutes=TOKEN_LIFETIME_MINUTES)}
    return jwt.encode(claims, secret, algorithm=JWT_ALGORITHM)


def decode_token(token: str, secret: str) -> dict:
    """Raises jwt.InvalidTokenError (or a subclass like ExpiredSignatureError) if invalid."""
    return jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
