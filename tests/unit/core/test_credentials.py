"""
Tests for Pytre/credentials.py

REGRESSION TESTS:
- Binary file mode for encryption/decryption (CRITICAL SECURITY FIX)
"""

import pytest
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken

from Pytre_Core.credentials import (
    create_key_and_export,
    retrieve_key,
    crypt_to_file,
    decrypt_from_file,
    crypted_file_pwd_change,
    crypted_file_pwd_history,
    crypted_file_pwd_get,
)


# ================================================================================
# REGRESSION TESTS - Binary File Mode
# ================================================================================


@pytest.mark.security
@pytest.mark.regression
def test_decrypt_reads_binary_mode(tmp_path, test_encryption_key):
    """
    Regression test: must use 'rb' not 'r'

    CRITICAL: decrypt_from_file() must open file in binary mode.
    Previous bug: opened in text mode, causing TypeError when decrypt() received string.
    """
    # Setup: Create encrypted file
    file_path = tmp_path / "encrypted.bin"
    cipher = Fernet(test_encryption_key)
    encrypted = cipher.encrypt(b"secret data")
    file_path.write_bytes(encrypted)

    # Test: Should NOT raise TypeError about str vs bytes
    result = decrypt_from_file(file_path, test_encryption_key)

    # Verify
    assert result == "secret data"
    assert isinstance(result, str)


@pytest.mark.security
@pytest.mark.regression
def test_retrieve_key_reads_binary_mode(tmp_path):
    """
    Regression test: retrieve_key must use 'rb'

    CRITICAL: Key file must be read as bytes, not string.
    """
    # Setup: Create key file
    key_file = tmp_path / "test.key"
    test_key = Fernet.generate_key()
    key_file.write_bytes(test_key)

    # Test
    result = retrieve_key(key_file)

    # Verify: Should return bytes
    assert isinstance(result, bytes)
    assert result == test_key


@pytest.mark.security
def test_encryption_roundtrip(tmp_path, test_encryption_key):
    """
    Encrypt then decrypt should return original text.

    This validates the complete encryption/decryption cycle with binary mode.
    """
    file_path = tmp_path / "test.enc"
    original = "test password with unicode: éàç"

    # Encrypt
    crypt_to_file(file_path, test_encryption_key, original)

    # Decrypt
    result = decrypt_from_file(file_path, test_encryption_key)

    # Verify
    assert result == original


@pytest.mark.security
def test_encryption_handles_unicode(tmp_path, test_encryption_key):
    """Test encryption/decryption with various unicode characters"""
    file_path = tmp_path / "unicode.enc"
    original = "Spécial chars: é à ç ñ ü 中文 🔐"

    crypt_to_file(file_path, test_encryption_key, original)
    result = decrypt_from_file(file_path, test_encryption_key)

    assert result == original


@pytest.mark.security
def test_encryption_handles_empty_string(tmp_path, test_encryption_key):
    """Test encryption/decryption with empty string"""
    file_path = tmp_path / "empty.enc"
    original = ""

    crypt_to_file(file_path, test_encryption_key, original)
    result = decrypt_from_file(file_path, test_encryption_key)

    assert result == original


# ================================================================================
# Decryption Error Handling
# ================================================================================


def test_decrypt_nonexistent_file_raises_error(test_encryption_key):
    """decrypt_from_file should raise FileNotFoundError for missing file"""
    nonexistent = Path("/nonexistent/file.enc")

    with pytest.raises(FileNotFoundError):
        decrypt_from_file(nonexistent, test_encryption_key)


def test_decrypt_corrupted_file_raises_invalidtoken(tmp_path, test_encryption_key):
    """decrypt_from_file should raise InvalidToken for corrupted data"""
    corrupted_file = tmp_path / "corrupted.enc"
    corrupted_file.write_bytes(b"this is not encrypted data")

    with pytest.raises(InvalidToken):
        decrypt_from_file(corrupted_file, test_encryption_key)


def test_decrypt_with_wrong_key_raises_invalidtoken(tmp_path):
    """Decrypting with wrong key should raise InvalidToken"""
    file_path = tmp_path / "encrypted.bin"

    # Encrypt with one key
    key1 = Fernet.generate_key()
    cipher1 = Fernet(key1)
    encrypted = cipher1.encrypt(b"secret")
    file_path.write_bytes(encrypted)

    # Try to decrypt with different key
    key2 = Fernet.generate_key()

    with pytest.raises(InvalidToken):
        decrypt_from_file(file_path, key2)


# ================================================================================
# Key Creation and Export
# ================================================================================


def test_create_key_and_export_creates_file(tmp_path):
    """create_key_and_export should create a valid key file"""
    key_file = tmp_path / "new.key"

    create_key_and_export(key_file)

    assert key_file.exists()
    assert key_file.stat().st_size > 0


def test_create_key_generates_valid_fernet_key(tmp_path):
    """Generated key should be valid for Fernet encryption"""
    key_file = tmp_path / "test.key"

    create_key_and_export(key_file)
    key = retrieve_key(key_file)

    # Should not raise error
    cipher = Fernet(key)

    # Should be able to encrypt/decrypt
    test_data = b"test"
    encrypted = cipher.encrypt(test_data)
    decrypted = cipher.decrypt(encrypted)

    assert decrypted == test_data


# ================================================================================
# Password History Management
# ================================================================================


def test_crypted_file_pwd_change_creates_file(tmp_path, monkeypatch):
    """crypted_file_pwd_change should create password history file"""
    pwd_file = tmp_path / "pwd_history.enc"

    # Mock the KEY_FILE constant
    import Pytre_Core.credentials as cred_module
    monkeypatch.setattr(cred_module, "KEY_FILE", pwd_file)

    crypted_file_pwd_change("password123")

    assert pwd_file.exists()


def test_crypted_file_pwd_history_keeps_25_passwords(tmp_path, monkeypatch):
    """Password history should keep maximum 25 passwords"""
    pwd_file = tmp_path / "pwd_history.enc"

    import Pytre_Core.credentials as cred_module
    monkeypatch.setattr(cred_module, "KEY_FILE", pwd_file)

    # Add 30 passwords
    for i in range(30):
        crypted_file_pwd_change(f"password{i}")

    # Retrieve history
    history = crypted_file_pwd_history()

    # Should only keep 25 most recent
    assert len(history) == 25
    assert history[0] == "password29"  # Most recent first
    assert history[-1] == "password5"  # Oldest kept


def test_crypted_file_pwd_history_empty_file_returns_empty_list(tmp_path, monkeypatch):
    """crypted_file_pwd_history should return empty list if file doesn't exist"""
    nonexistent = tmp_path / "nonexistent.enc"

    import Pytre_Core.credentials as cred_module
    monkeypatch.setattr(cred_module, "KEY_FILE", nonexistent)

    history = crypted_file_pwd_history()

    assert history == []


def test_crypted_file_pwd_get_returns_latest(tmp_path, monkeypatch):
    """crypted_file_pwd_get should return the most recent password"""
    pwd_file = tmp_path / "pwd_history.enc"

    import Pytre_Core.credentials as cred_module
    monkeypatch.setattr(cred_module, "KEY_FILE", pwd_file)

    # Add passwords
    crypted_file_pwd_change("password1")
    crypted_file_pwd_change("password2")
    crypted_file_pwd_change("password3")

    # Get latest
    latest = crypted_file_pwd_get()

    assert latest == "password3"


def test_crypted_file_pwd_get_empty_returns_empty_string(tmp_path, monkeypatch):
    """crypted_file_pwd_get should return empty string if no password"""
    nonexistent = tmp_path / "nonexistent.enc"

    import Pytre_Core.credentials as cred_module
    monkeypatch.setattr(cred_module, "KEY_FILE", nonexistent)

    result = crypted_file_pwd_get()

    assert result == ""


# ================================================================================
# Edge Cases
# ================================================================================


def test_crypt_with_multiline_text(tmp_path, test_encryption_key):
    """Test encryption of multiline text"""
    file_path = tmp_path / "multiline.enc"
    original = """Line 1
Line 2
Line 3
With special chars: éàç"""

    crypt_to_file(file_path, test_encryption_key, original)
    result = decrypt_from_file(file_path, test_encryption_key)

    assert result == original


def test_crypt_with_very_long_text(tmp_path, test_encryption_key):
    """Test encryption of very long text"""
    file_path = tmp_path / "long.enc"
    original = "A" * 10000

    crypt_to_file(file_path, test_encryption_key, original)
    result = decrypt_from_file(file_path, test_encryption_key)

    assert result == original
    assert len(result) == 10000
