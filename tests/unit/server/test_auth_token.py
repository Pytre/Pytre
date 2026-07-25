"""
Tests for Pytre_Server/auth_token.py
"""

import secrets
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from Pytre_Server.auth_token import JWT_ALGORITHM, create_token, decode_token


@pytest.mark.unit
def test_create_and_decode_token_round_trips():
    secret = secrets.token_urlsafe(32)

    token = create_token("alice", secret)
    claims = decode_token(token, secret)

    assert claims["sub"] == "alice"
    assert "iat" in claims
    assert "exp" in claims


@pytest.mark.unit
def test_decode_token_rejects_wrong_secret():
    secret = secrets.token_urlsafe(32)
    token = create_token("alice", secret)

    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token, secrets.token_urlsafe(32))


@pytest.mark.unit
def test_decode_token_rejects_expired_token():
    secret = secrets.token_urlsafe(32)
    expired_claims = {
        "sub": "alice",
        "iat": datetime.now(timezone.utc) - timedelta(minutes=40),
        "exp": datetime.now(timezone.utc) - timedelta(minutes=10),
    }
    token = jwt.encode(expired_claims, secret, algorithm=JWT_ALGORITHM)

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(token, secret)


@pytest.mark.unit
def test_decode_token_rejects_garbage_token():
    with pytest.raises(jwt.InvalidTokenError):
        decode_token("not-a-token", secrets.token_urlsafe(32))
