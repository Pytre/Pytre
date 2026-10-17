import keyring
import keyring.errors
import pytest
from pathlib import Path
from unittest.mock import Mock, MagicMock
from cryptography.fernet import Fernet


# ================================================================================
# Fixtures pour tests de fichiers
# ================================================================================


@pytest.fixture
def temp_dir(tmp_path):
    """Temporary directory for test files"""
    return tmp_path


# ================================================================================
# System keyring isolation
# ================================================================================


@pytest.fixture(autouse=True)
def fake_keyring(monkeypatch):
    """In-memory stand-in for the OS keyring backend, applied to every test - never touches the
    real system credential store, and works on machines without any backend (CI runners)."""
    store: dict[tuple[str, str], str] = {}

    def fake_get_password(service, username):
        return store.get((service, username))

    def fake_set_password(service, username, password):
        store[(service, username)] = password

    def fake_delete_password(service, username):
        if (service, username) not in store:
            raise keyring.errors.PasswordDeleteError("not found")
        del store[(service, username)]

    monkeypatch.setattr(keyring, "get_password", fake_get_password)
    monkeypatch.setattr(keyring, "set_password", fake_set_password)
    monkeypatch.setattr(keyring, "delete_password", fake_delete_password)

    return store


# ================================================================================
# Fixtures pour tests de cryptage (credentials.py)
# ================================================================================


@pytest.fixture
def test_encryption_key():
    """Generate test Fernet key"""
    return Fernet.generate_key()


@pytest.fixture
def encrypted_test_file(tmp_path, test_encryption_key):
    """Create encrypted file for testing"""
    file_path = tmp_path / "test_encrypted.bin"
    cipher = Fernet(test_encryption_key)
    encrypted = cipher.encrypt(b"test password")
    file_path.write_bytes(encrypted)
    return file_path


# ================================================================================
# Fixtures pour tests KeePass (mock database)
# ================================================================================


@pytest.fixture
def mock_keepass_db():
    """Mock KeePass database for testing without real DB dependency"""
    mock_db = Mock()
    mock_db.root_group = Mock()

    # Mock groups
    mock_settings_group = Mock()
    mock_users_group = Mock()
    mock_servers_group = Mock()

    mock_db.find_groups.return_value = [mock_settings_group, mock_users_group, mock_servers_group]

    return mock_db


@pytest.fixture
def mock_kee_entry():
    """Mock KeePass entry for testing"""
    mock_entry = Mock()
    mock_entry.username = "test_user"
    mock_entry.password = "test_password"
    mock_entry.title = "Test Entry"
    mock_entry.custom_properties = {}
    mock_entry.tags = []

    def get_custom_property(key):
        return mock_entry.custom_properties.get(key, None)

    def set_custom_property(key, value):
        mock_entry.custom_properties[key] = value

    mock_entry.get_custom_property = get_custom_property
    mock_entry.set_custom_property = set_custom_property

    return mock_entry


# ================================================================================
# Fixtures pour tests SQL query
# ================================================================================


@pytest.fixture
def sample_query_file(tmp_path):
    """Create sample SQL query file with metadata for testing"""
    query_file = tmp_path / "test_query.sql"
    content = """-- code: TEST01
-- description: Test Query
-- servers: SERVER1, SERVER2
-- groups: ADMIN
-- hide: 0

DECLARE
@param_id int as 123 -- ID parameter
@param_name nvarchar(50) as N'test' -- Name parameter
;

SELECT * FROM TestTable
WHERE id = @param_id AND name = @param_name
"""
    query_file.write_text(content, encoding="utf-8")
    return query_file


@pytest.fixture
def sample_csv_servers(tmp_path):
    """Create sample CSV for server import testing"""
    csv_file = tmp_path / "servers.csv"
    content = """id;server_type;host;port;database
TEST_MSSQL;MSSQL;localhost;1433;testdb
TEST_PG;PostgreSQL;localhost;5432;testdb"""
    csv_file.write_text(content, encoding="latin-1")
    return csv_file


@pytest.fixture
def sample_csv_users(tmp_path):
    """Create sample CSV for user import testing"""
    csv_file = tmp_path / "users.csv"
    content = """id;description;admin;groups
user1;Test User 1;1;admin,users
user2;Test User 2;0;users"""
    csv_file.write_text(content, encoding="latin-1")
    return csv_file
