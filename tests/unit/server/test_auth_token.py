"""
Tests for Pytre_Server/auth_token.py
"""

import secrets
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from Pytre_Server.auth_token import JWT_ALGORITHM, TokenType, create_token, decode_token


@pytest.mark.unit
def test_create_and_decode_token_round_trips():
    secret = secrets.token_urlsafe(32)

    token = create_token("alice", secret, TokenType.ACCESS)
    claims = decode_token(token, secret, TokenType.ACCESS)

    assert claims["sub"] == "alice"
    assert claims["type"] == "ACCESS"
    assert "iat" in claims
    assert "exp" in claims


@pytest.mark.unit
def test_create_token_sets_expiry_from_token_type():
    secret = secrets.token_urlsafe(32)

    access_claims = decode_token(create_token("alice", secret, TokenType.ACCESS), secret, TokenType.ACCESS)
    refresh_claims = decode_token(create_token("alice", secret, TokenType.REFRESH), secret, TokenType.REFRESH)

    access_lifetime = access_claims["exp"] - access_claims["iat"]
    refresh_lifetime = refresh_claims["exp"] - refresh_claims["iat"]
    assert refresh_lifetime > access_lifetime


@pytest.mark.unit
def test_decode_token_rejects_wrong_secret():
    secret = secrets.token_urlsafe(32)
    token = create_token("alice", secret, TokenType.ACCESS)

    with pytest.raises(jwt.InvalidTokenError):
        decode_token(token, secrets.token_urlsafe(32), TokenType.ACCESS)


@pytest.mark.unit
def test_decode_token_rejects_expired_token():
    secret = secrets.token_urlsafe(32)
    expired_claims = {
        "sub": "alice",
        "iat": datetime.now(timezone.utc) - timedelta(minutes=40),
        "exp": datetime.now(timezone.utc) - timedelta(minutes=10),
        "type": TokenType.ACCESS.name,
    }
    token = jwt.encode(expired_claims, secret, algorithm=JWT_ALGORITHM)

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(token, secret, TokenType.ACCESS)


@pytest.mark.unit
def test_decode_token_rejects_garbage_token():
    with pytest.raises(jwt.InvalidTokenError):
        decode_token("not-a-token", secrets.token_urlsafe(32), TokenType.ACCESS)


@pytest.mark.unit
def test_decode_token_rejects_refresh_token_used_as_access_token():
    """The "type" claim stops a long-lived refresh token from being usable as a short-lived
    access token (or vice versa) - a stolen refresh token alone must not grant API access."""
    secret = secrets.token_urlsafe(32)
    refresh_token = create_token("alice", secret, TokenType.REFRESH)

    with pytest.raises(jwt.InvalidTokenError):
        decode_token(refresh_token, secret, TokenType.ACCESS)


@pytest.mark.unit
def test_decode_token_rejects_access_token_used_as_refresh_token():
    secret = secrets.token_urlsafe(32)
    access_token = create_token("alice", secret, TokenType.ACCESS)

    with pytest.raises(jwt.InvalidTokenError):
        decode_token(access_token, secret, TokenType.REFRESH)


@pytest.mark.unit
def test_create_and_decode_admin_token_round_trips():
    secret = secrets.token_urlsafe(32)

    token = create_token("alice", secret, TokenType.ADMIN)
    claims = decode_token(token, secret, TokenType.ADMIN)

    assert claims["sub"] == "alice"
    assert claims["type"] == "ADMIN"


@pytest.mark.unit
def test_decode_token_rejects_admin_token_used_as_access_token():
    """The admin webui session token must not double as an API access token, or vice versa -
    the two surfaces (browser cookie session, JSON API bearer token) must stay isolated."""
    secret = secrets.token_urlsafe(32)
    admin_token = create_token("alice", secret, TokenType.ADMIN)

    with pytest.raises(jwt.InvalidTokenError):
        decode_token(admin_token, secret, TokenType.ACCESS)


@pytest.mark.unit
def test_admin_token_lifetime_is_eight_hours():
    """The absolute cap on an admin webui session - enforced natively by `exp`, independently of
    the inactivity timeout below."""
    assert TokenType.ADMIN.value == timedelta(hours=8)


@pytest.mark.unit
def test_admin_session_timeout_is_one_hour():
    assert TokenType.ADMIN.session_timeout == timedelta(hours=1)


@pytest.mark.unit
def test_access_and_refresh_tokens_have_no_session_timeout():
    """ACCESS/REFRESH have no notion of inactivity - only `exp` ever governs their validity."""
    assert TokenType.ACCESS.session_timeout is None
    assert TokenType.REFRESH.session_timeout is None
