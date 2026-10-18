from datetime import datetime, timedelta, timezone
from enum import Enum

import jwt


JWT_ALGORITHM = "HS256"


class TokenType(Enum):
    ACCESS = timedelta(minutes=30)
    REFRESH = timedelta(days=30)
    ADMIN = timedelta(hours=8)

    @property
    def session_timeout(self) -> timedelta | None:
        """Inactivity timeout that should be applied to revoke token before its expiration.
        It's up to the caller to enforce it."""
        return {
            TokenType.ADMIN: timedelta(hours=1),
        }.get(self)


def create_token(username: str, key: str, type: TokenType) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": username, "iat": now, "exp": now + type.value, "type": type.name}
    return jwt.encode(payload, key, algorithm=JWT_ALGORITHM)


def decode_token(token: str, key: str, expected_type: TokenType) -> dict:
    """Raises jwt.InvalidTokenError if invalid/expired, or if it is not an `expected_type` token."""
    payload = jwt.decode(token, key, algorithms=[JWT_ALGORITHM])
    token_type = payload.get("type")
    if token_type != expected_type.name:
        raise jwt.InvalidTokenError(f"Expected a {expected_type.name} token, got {token_type}")
    return payload


if __name__ == "__main__":
    from Pytre_Core.context import AppContext, AppMode
    from Pytre_Server.master_key import get_master_key_interactive

    master_key: str | None = get_master_key_interactive()

    if master_key:
        app_ctx = AppContext(master_key=master_key, mode=AppMode.SERVER)
        token = create_token("dummy_user", app_ctx.settings.jwt_secret, TokenType.ACCESS)
        decoded = jwt.decode(token, options={"verify_signature": False})
        print(decoded)
