"""
Tests for Pytre/servers.py

REGRESSION TESTS:
- CSV import validation (delimiter, quotechar, file existence)
- Optimized list search with generators (csv_import line 96)
"""

import pytest
import csv
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from Pytre_Core.servers import Server, Servers, filter_servers


# ================================================================================
# Fixtures
# ================================================================================


@pytest.fixture
def mock_kee():
    """Mock Kee object for Servers"""
    mock = Mock()
    mock.is_ko = False
    mock.db = Mock()
    mock.grp_servers = Mock()
    mock.grp_servers.entries = []
    mock.grp_users = Mock()
    mock.grp_users.entries = []
    mock.open_db = Mock(return_value=True)
    mock.save_db = Mock()
    return mock


@pytest.fixture
def sample_csv_servers(tmp_path):
    """Create sample CSV file for testing"""
    csv_file = tmp_path / "servers.csv"
    content = """id;description;user;password;grp_authorized;type;charset;database;host;port;server;login_timeout;timeout
TEST_MSSQL;Test MSSQL Server;sa;password;admin;MSSQL;utf8;testdb;localhost;1433;SQLSERVER;5;30
TEST_PG;Test PostgreSQL;postgres;password;users;PostgreSQL;utf8;testdb;localhost;5432;POSTGRES;5;30"""

    csv_file.write_text(content, encoding="latin-1")
    return csv_file


# ================================================================================
# REGRESSION TESTS - CSV Import Validation
# ================================================================================


@pytest.mark.regression
def test_csv_import_validates_delimiter(mock_kee):
    """
    Regression test: delimiter must be valid

    Delimiter must be single character and different from quotechar.
    """
    servers = Servers(mock_kee)

    # Multiple characters
    with pytest.raises(ValueError, match="single character"):
        servers.csv_import("test.csv", delimiter=";;")

    # Same as quotechar
    with pytest.raises(ValueError, match="different from quote"):
        servers.csv_import("test.csv", delimiter='"', quotechar='"')


@pytest.mark.regression
def test_csv_import_validates_quotechar(mock_kee):
    """Quotechar must be single character"""
    servers = Servers(mock_kee)

    with pytest.raises(ValueError, match="single character"):
        servers.csv_import("test.csv", delimiter=";", quotechar='""')


@pytest.mark.regression
def test_csv_import_validates_file_exists(mock_kee):
    """CSV import should validate file existence"""
    servers = Servers(mock_kee)

    with pytest.raises(FileNotFoundError, match="does not exist"):
        servers.csv_import("/nonexistent/servers.csv")


@pytest.mark.regression
def test_csv_import_validates_required_columns(tmp_path, mock_kee):
    """CSV import should validate required columns exist"""
    servers = Servers(mock_kee)

    # Create CSV with missing columns
    csv_file = tmp_path / "incomplete.csv"
    content = "id;description;user\nTEST;Test Server;admin"
    csv_file.write_text(content, encoding="latin-1")

    with pytest.raises(ValueError, match="Missing columns in file to import"):
        servers.csv_import(csv_file)


# ================================================================================
# REGRESSION TESTS - Optimized Search
# ================================================================================


@pytest.mark.regression
def test_csv_import_uses_generator_for_search(tmp_path, mock_kee):
    """
    Regression test: use generator with next()

    The search for existing entry uses next() with generator
    expression for early termination (line 96).
    """

    # Create existing entries
    existing_entry = Mock()
    existing_entry.title = "EXISTING_SERVER"
    existing_entry.username = "old_user"
    existing_entry.password = "old_pass"
    existing_entry.notes = "old_desc"
    existing_entry.tags = []
    existing_entry.custom_properties = []
    existing_entry.set_custom_property = Mock()
    existing_entry.get_custom_property = Mock(return_value=None)

    mock_kee.grp_servers.entries = [existing_entry]

    servers = Servers(mock_kee)

    # Create CSV with entry that matches existing (case-insensitive)
    csv_file = tmp_path / "update.csv"
    headers = [
        "id",
        "description",
        "user",
        "password",
        "grp_authorized",
        "type",
        "charset",
        "database",
        "host",
        "port",
        "server",
        "login_timeout",
        "timeout",
    ]
    row = [
        "existing_server",
        "Updated Server",
        "new_user",
        "new_pass",
        "",
        "MSSQL",
        "utf8",
        "db",
        "host",
        "1433",
        "srv",
        "5",
        "30",
    ]

    with open(csv_file, "w", encoding="latin-1", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, delimiter=";")
        writer.writeheader()
        writer.writerow(dict(zip(headers, row)))

    # Import should update existing entry (not create new one)
    servers.csv_import(csv_file)

    # Verify: Existing entry was updated (username changed)
    assert existing_entry.username == "new_user"

    # Verify: No new entry was created (add_entry not called)
    assert not mock_kee.db.add_entry.called


# ================================================================================
# CSV Import Functionality
# ================================================================================


@pytest.mark.unit
def test_csv_import_creates_new_entries(tmp_path, mock_kee):
    """CSV import should create new entries if they don't exist"""
    servers = Servers(mock_kee)

    # Mock add_entry to return new entry
    new_entry = Mock()
    new_entry.title = ""
    new_entry.username = ""
    new_entry.password = ""
    new_entry.notes = ""
    new_entry.tags = []
    new_entry.set_custom_property = Mock()

    mock_kee.db.add_entry = Mock(return_value=new_entry)

    # Create CSV with new server
    csv_file = tmp_path / "new.csv"
    headers = [
        "id",
        "description",
        "user",
        "password",
        "grp_authorized",
        "type",
        "charset",
        "database",
        "host",
        "port",
        "server",
        "login_timeout",
        "timeout",
    ]
    row = [
        "NEW_SERVER",
        "New Server",
        "admin",
        "pass",
        "admin",
        "MSSQL",
        "utf8",
        "db",
        "host",
        "1433",
        "srv",
        "5",
        "30",
    ]

    with open(csv_file, "w", encoding="latin-1", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, delimiter=";")
        writer.writeheader()
        writer.writerow(dict(zip(headers, row)))

    # Import
    servers.csv_import(csv_file)

    # Verify: add_entry was called
    assert mock_kee.db.add_entry.called

    # Verify: Entry properties were set
    assert new_entry.title == "NEW_SERVER"
    assert new_entry.username == "admin"


@pytest.mark.unit
def test_csv_import_preserves_password_when_column_blank(tmp_path, mock_kee):
    """Regression: csv_import() used to unconditionally overwrite the password from the CSV row,
    so re-importing an existing server with a blank password column silently blanked out a real,
    already-configured password - mirrors the same fix already applied to Remotes/Users csv_import."""
    existing_entry = Mock()
    existing_entry.title = "EXISTING_SERVER"
    existing_entry.username = "admin"
    existing_entry.password = "old_secret"
    existing_entry.notes = ""
    existing_entry.tags = []
    existing_entry.get_custom_property = Mock(return_value=None)
    existing_entry.set_custom_property = Mock()
    mock_kee.grp_servers.entries = [existing_entry]

    servers = Servers(mock_kee)

    csv_file = tmp_path / "update.csv"
    headers = [
        "id",
        "description",
        "user",
        "password",
        "grp_authorized",
        "type",
        "charset",
        "database",
        "host",
        "port",
        "server",
        "login_timeout",
        "timeout",
    ]
    row = [
        "EXISTING_SERVER",
        "Updated description",
        "admin",
        "",  # blank password column - existing password must be preserved
        "",
        "MSSQL",
        "utf8",
        "db",
        "host",
        "1433",
        "srv",
        "5",
        "30",
    ]

    with open(csv_file, "w", encoding="latin-1", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, delimiter=";")
        writer.writeheader()
        writer.writerow(dict(zip(headers, row)))

    servers.csv_import(csv_file)

    assert existing_entry.password == "old_secret"


@pytest.mark.unit
def test_csv_import_parses_groups_with_delimiter(tmp_path, mock_kee):
    """CSV import should correctly parse groups containing delimiter"""
    servers = Servers(mock_kee)

    new_entry = Mock()
    new_entry.title = ""
    new_entry.username = ""
    new_entry.password = ""
    new_entry.notes = ""
    new_entry.tags = []
    new_entry.set_custom_property = Mock()
    mock_kee.db.add_entry = Mock(return_value=new_entry)

    # Create CSV with quoted groups containing delimiter
    csv_file = tmp_path / "groups.csv"
    headers = [
        "id",
        "description",
        "user",
        "password",
        "grp_authorized",
        "type",
        "charset",
        "database",
        "host",
        "port",
        "server",
        "login_timeout",
        "timeout",
    ]
    # Groups with delimiter in quotes
    row = [
        "SERVER1",
        "Server",
        "user",
        "pass",
        '"admin;users;power-users"',
        "MSSQL",
        "utf8",
        "db",
        "host",
        "1433",
        "srv",
        "5",
        "30",
    ]

    with open(csv_file, "w", encoding="latin-1", newline="") as f:
        writer = csv.writer(f, delimiter=";", quotechar='"')
        writer.writerow(headers)
        writer.writerow(row)

    # Import
    servers.csv_import(csv_file)

    # Verify: Groups were split correctly
    # Semicolons in groups should be replaced with underscores
    assert "admin_users_power-users" in new_entry.tags or len(new_entry.tags) > 1


@pytest.mark.unit
def test_csv_import_replaces_invalid_chars_in_tags(tmp_path, mock_kee):
    """CSV import should replace invalid characters in tags"""
    servers = Servers(mock_kee)

    new_entry = Mock()
    new_entry.title = ""
    new_entry.username = ""
    new_entry.password = ""
    new_entry.notes = ""
    new_entry.tags = []
    new_entry.set_custom_property = Mock()
    mock_kee.db.add_entry = Mock(return_value=new_entry)

    csv_file = tmp_path / "tags.csv"
    headers = [
        "id",
        "description",
        "user",
        "password",
        "grp_authorized",
        "type",
        "charset",
        "database",
        "host",
        "port",
        "server",
        "login_timeout",
        "timeout",
    ]
    row = [
        "SERVER1",
        "Server",
        "user",
        "pass",
        "admin,users;power",
        "MSSQL",
        "utf8",
        "db",
        "host",
        "1433",
        "srv",
        "5",
        "30",
    ]

    with open(csv_file, "w", encoding="latin-1", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, delimiter=";")
        writer.writeheader()
        writer.writerow(dict(zip(headers, row)))

    servers.csv_import(csv_file)

    # Verify: Commas and semicolons replaced with underscores
    for tag in new_entry.tags:
        assert "," not in tag
        assert ";" not in tag


# ================================================================================
# CSV Export Functionality
# ================================================================================


@pytest.mark.unit
def test_csv_export_validates_delimiter(mock_kee, tmp_path):
    """CSV export should validate delimiter"""
    servers = Servers(mock_kee)

    output_file = tmp_path / "export.csv"

    with pytest.raises(ValueError, match="single character"):
        servers.csv_export(output_file, delimiter=";;")


@pytest.mark.unit
def test_csv_export_validates_file_not_exists_without_overwrite(mock_kee, tmp_path):
    """CSV export should not overwrite existing file by default"""
    servers = Servers(mock_kee)

    # Create existing file
    existing_file = tmp_path / "existing.csv"
    existing_file.write_text("existing data")

    with pytest.raises(FileExistsError, match="already exists"):
        servers.csv_export(existing_file, overwrite=False)


@pytest.mark.unit
def test_csv_export_allows_overwrite_when_flag_set(mock_kee, tmp_path):
    """CSV export should allow overwrite when flag is True"""

    # Add mock entry
    entry = Mock()
    entry.title = "TEST"
    entry.notes = "Test Server"
    entry.username = "user"
    entry.password = "pass"
    entry.tags = []
    entry.custom_properties = []
    entry.get_custom_property = Mock(return_value="")
    mock_kee.grp_servers.entries = [entry]

    servers = Servers(mock_kee)

    # Create existing file
    existing_file = tmp_path / "existing.csv"
    existing_file.write_text("old data")

    # Should not raise error
    result = servers.csv_export(existing_file, overwrite=True)

    assert result is True
    # File should be overwritten
    assert "old data" not in existing_file.read_text()


# ================================================================================
# Get All Servers Functionality
# ================================================================================


@pytest.mark.unit
def test_get_all_servers_returns_dict(mock_kee):
    """get_all_servers should return dict of Server objects"""
    # Add mock entries
    entry1 = Mock()
    entry1.uuid = "uuid-1"
    entry1.title = "SERVER1"
    entry1.username = "user1"
    entry1.password = "pass1"
    entry1.notes = "Server 1"
    entry1.tags = ["admin"]
    entry1.custom_properties = []
    entry1.get_custom_property = Mock(return_value=None)

    entry2 = Mock()
    entry2.uuid = "uuid-2"
    entry2.title = "SERVER2"
    entry2.username = "user2"
    entry2.password = "pass2"
    entry2.notes = "Server 2"
    entry2.tags = ["users"]
    entry2.custom_properties = []
    entry2.get_custom_property = Mock(return_value=None)

    mock_kee.grp_servers.entries = [entry1, entry2]

    servers = Servers(mock_kee)

    result = servers.get_all_servers()

    assert isinstance(result, dict)
    assert len(result) == 2
    assert "SERVER1" in result
    assert "SERVER2" in result


@pytest.mark.unit
def test_get_all_servers_with_group_filter(mock_kee):
    """get_all_servers should filter by group"""
    # Add mock entries
    entry1 = Mock()
    entry1.uuid = "uuid-1"
    entry1.title = "SERVER1"
    entry1.username = "user1"
    entry1.password = "pass1"
    entry1.notes = "Server 1"
    entry1.tags = ["admin"]
    entry1.custom_properties = []
    entry1.get_custom_property = Mock(return_value=None)

    entry2 = Mock()
    entry2.uuid = "uuid-2"
    entry2.title = "SERVER2"
    entry2.username = "user2"
    entry2.password = "pass2"
    entry2.notes = "Server 2"
    entry2.tags = ["users"]
    entry2.custom_properties = []
    entry2.get_custom_property = Mock(return_value=None)

    mock_kee.grp_servers.entries = [entry1, entry2]

    servers = Servers(mock_kee)

    result = servers.get_all_servers(grp_filter=["admin"])

    # Should only include SERVER1 (has admin tag), SERVER2 must be excluded
    assert set(result.keys()) == {"SERVER1"}


@pytest.mark.unit
def test_get_all_servers_caches_filtered_result_on_attribute(mock_kee):
    """The servers_dict attribute must reflect the filtered view after a filtered
    call - Pytre_Client relies on this (single user per process, e.g. app.py's
    "hide server selector if len(servers.servers_dict) <= 1" check), not just on
    the return value of get_all_servers()."""
    entry1 = Mock(uuid="uuid-1", title="SERVER1", username="u1", password="p1", notes="", tags=["admin"])
    entry1.custom_properties = []
    entry1.get_custom_property = Mock(return_value=None)
    entry2 = Mock(uuid="uuid-2", title="SERVER2", username="u2", password="p2", notes="", tags=["users"])
    entry2.custom_properties = []
    entry2.get_custom_property = Mock(return_value=None)
    mock_kee.grp_servers.entries = [entry1, entry2]

    servers = Servers(mock_kee)
    servers.get_all_servers(grp_filter=["admin"])

    assert set(servers.servers_dict.keys()) == {"SERVER1"}


@pytest.mark.unit
def test_get_all_servers_without_filter_keeps_full_list_on_attribute(mock_kee):
    """A later unfiltered refresh must restore servers_dict to the complete list -
    this is what Pytre_Server relies on to keep its shared instance always unfiltered."""
    entry1 = Mock(uuid="uuid-1", title="SERVER1", username="u1", password="p1", notes="", tags=["admin"])
    entry1.custom_properties = []
    entry1.get_custom_property = Mock(return_value=None)
    entry2 = Mock(uuid="uuid-2", title="SERVER2", username="u2", password="p2", notes="", tags=["users"])
    entry2.custom_properties = []
    entry2.get_custom_property = Mock(return_value=None)
    mock_kee.grp_servers.entries = [entry1, entry2]

    servers = Servers(mock_kee)
    servers.get_all_servers(grp_filter=["admin"])
    servers.get_all_servers()  # no filter - must restore the full list

    assert set(servers.servers_dict.keys()) == {"SERVER1", "SERVER2"}


# ================================================================================
# Filter Servers Functionality (standalone, pure function)
# ================================================================================


@pytest.mark.unit
def test_filter_servers_returns_same_dict_when_filter_is_none():
    server1 = Mock(id="SRV1", grp_authorized=["admin"])
    servers_dict: dict[str, Server] = {"SRV1": server1}

    result = filter_servers(servers_dict, None)

    assert result is servers_dict


@pytest.mark.unit
def test_filter_servers_includes_server_with_no_restriction():
    server1 = Mock(id="SRV1", grp_authorized=[])
    servers_dict: dict[str, Server] = {"SRV1": server1}

    result = filter_servers(servers_dict, ["admin"])

    assert "SRV1" in result


@pytest.mark.unit
def test_filter_servers_includes_server_with_matching_group():
    server1 = Mock(id="SRV1", grp_authorized=["admin", "users"])
    servers_dict: dict[str, Server] = {"SRV1": server1}

    result = filter_servers(servers_dict, ["admin"])

    assert "SRV1" in result


@pytest.mark.unit
def test_filter_servers_excludes_server_without_matching_group():
    server1 = Mock(id="SRV1", grp_authorized=["restricted"])
    servers_dict: dict[str, Server] = {"SRV1": server1}

    result = filter_servers(servers_dict, ["admin"])

    assert result == {}


@pytest.mark.unit
def test_filter_servers_handles_mixed_set():
    allowed = Mock(id="SRV1", grp_authorized=["admin"])
    unrestricted = Mock(id="SRV2", grp_authorized=[])
    denied = Mock(id="SRV3", grp_authorized=["restricted"])
    servers_dict: dict[str, Server] = {"SRV1": allowed, "SRV2": unrestricted, "SRV3": denied}

    result = filter_servers(servers_dict, ["admin"])

    assert set(result.keys()) == {"SRV1", "SRV2"}


# ================================================================================
# Get All Groups Functionality
# ================================================================================


@pytest.mark.unit
def test_get_all_groups_returns_set(mock_kee):
    """get_all_groups should return set of all groups"""
    # Add mock entries with tags
    entry1 = Mock()
    entry1.uuid = "uuid-1"
    entry1.tags = ["admin", "users"]
    entry1.custom_properties = []
    entry1.get_custom_property = Mock(return_value=None)

    entry2 = Mock()
    entry2.uuid = "uuid-2"
    entry2.tags = ["power-users"]
    entry2.custom_properties = []
    entry2.get_custom_property = Mock(return_value=None)

    mock_kee.grp_servers.entries = [entry1, entry2]
    mock_kee.grp_users.entries = []

    servers = Servers(mock_kee)

    result = servers.get_all_groups()

    assert isinstance(result, set)
    assert "admin" in result
    assert "users" in result
    assert "power-users" in result


@pytest.mark.unit
def test_get_all_groups_handles_empty_tags(mock_kee):
    """get_all_groups should handle entries without tags"""
    entry = Mock()
    entry.uuid = "uuid-1"
    entry.tags = None
    entry.custom_properties = []
    entry.get_custom_property = Mock(return_value=None)

    mock_kee.grp_servers.entries = [entry]
    mock_kee.grp_users.entries = []

    servers = Servers(mock_kee)

    result = servers.get_all_groups()

    # Should not raise error
    assert isinstance(result, set)


# ================================================================================
# Database Open Functionality
# ================================================================================


@pytest.mark.unit
def test_open_db_returns_false_on_failure(mock_kee):
    """open_db should return False if database fails to open"""
    mock_kee.is_ko = True  # Database failed

    servers = Servers(mock_kee)
    result = servers.open_db()

    assert result is False


@pytest.mark.unit
def test_open_db_returns_true_on_success(mock_kee):
    """open_db should return True if database opens successfully"""
    servers = Servers(mock_kee)
    result = servers.open_db()

    assert result is True


# ================================================================================
# Server Class Tests
# ================================================================================


@pytest.mark.unit
def test_server_initialization():
    """Server should initialize with default values"""
    from Pytre_Core.servers import Server

    server = Server(Mock())

    assert server.id == ""
    assert server.description == ""
    assert server.user == ""
    assert server.password == ""
    assert server.type == "mssql"
    assert server.port == "1433"
    assert server.login_timeout == 60
    assert server.timeout == 300
    assert server.grp_authorized == []


@pytest.mark.unit
def test_server_to_dict():
    """Server should convert to dict"""
    from Pytre_Core.servers import Server

    server = Server(Mock())
    server.id = "TEST_SERVER"
    server.description = "Test Description"
    server.user = "admin"
    server.password = "secret"

    result = server.to_dict()

    assert isinstance(result, dict)
    assert result["id"] == "TEST_SERVER"
    assert result["description"] == "Test Description"
    assert result["user"] == "admin"
    assert result["password"] == "secret"
    assert "type" in result
    assert "port" in result


@pytest.mark.unit
def test_server_repr():
    """Server __repr__ should return string representation"""
    from Pytre_Core.servers import Server

    server = Server(Mock())
    server.id = "TEST"
    server.description = "Test Server"

    result = repr(server)

    assert isinstance(result, str)
    assert "TEST" in result
    assert "Test Server" in result


@pytest.mark.unit
def test_server_str():
    """Server __str__ should return string representation"""
    from Pytre_Core.servers import Server

    server = Server(Mock())
    server.id = "TEST"

    result = str(server)

    assert isinstance(result, str)
    assert "TEST" in result


@pytest.mark.unit
def test_server_type_enum_values():
    """ServerType enum should have correct values"""
    from Pytre_Core.servers import ServerType

    assert ServerType.mssql.value == "SQL Server"
    assert ServerType.postgre.value == "PostgreSQL"
    assert ServerType.mssql.name == "mssql"
    assert ServerType.postgre.name == "postgre"


# ================================================================================
# CSV Import/Export Edge Cases
# ================================================================================


@pytest.mark.unit
def test_csv_import_raises_if_db_not_opened(tmp_path, mock_kee):
    """csv_import should raise RuntimeError if the KeePass db failed to open"""
    servers = Servers(mock_kee)
    mock_kee.db = None

    csv_file = tmp_path / "servers.csv"
    csv_file.write_text("id;description;user;password;grp_authorized\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Database not opened"):
        servers.csv_import(csv_file)


@pytest.mark.unit
def test_csv_import_raises_if_encoding_not_detected(tmp_path, mock_kee):
    """csv_import should raise ValueError if the file's encoding can't be detected"""
    servers = Servers(mock_kee)

    csv_file = tmp_path / "servers.csv"
    csv_file.write_text("id;description;user;password;grp_authorized\n", encoding="utf-8")

    with patch("Pytre_Core.servers.detect_encoding", return_value=None):
        with pytest.raises(ValueError, match="Unable to detect encoding"):
            servers.csv_import(csv_file)


@pytest.mark.unit
def test_csv_import_empty_groups_clears_tags(tmp_path, mock_kee):
    """csv_import should set tags to an empty list when grp_authorized is blank"""
    servers = Servers(mock_kee)

    new_entry = Mock()
    new_entry.title = ""
    new_entry.username = ""
    new_entry.password = ""
    new_entry.notes = ""
    new_entry.tags = ["stale_tag"]
    new_entry.set_custom_property = Mock()
    mock_kee.db.add_entry = Mock(return_value=new_entry)

    csv_file = tmp_path / "no_groups.csv"
    headers = [
        "id",
        "description",
        "user",
        "password",
        "grp_authorized",
        "type",
        "charset",
        "database",
        "host",
        "port",
        "server",
        "login_timeout",
        "timeout",
    ]
    row = ["SERVER1", "Server", "user", "pass", "", "MSSQL", "utf8", "db", "host", "1433", "srv", "5", "30"]

    with open(csv_file, "w", encoding="latin-1", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, delimiter=";")
        writer.writeheader()
        writer.writerow(dict(zip(headers, row)))

    servers.csv_import(csv_file)

    assert new_entry.tags == []


@pytest.mark.unit
def test_csv_export_raises_if_no_group_selected(mock_kee, tmp_path):
    """csv_export should raise ValueError if no KeePass group is selected"""
    servers = Servers(mock_kee)
    servers.kee_grp = None

    output_file = tmp_path / "export.csv"

    with pytest.raises(ValueError, match="No KeePass group selected"):
        servers.csv_export(output_file)


# ================================================================================
# find_entry_by_uuid Functionality
# ================================================================================


@pytest.mark.unit
def test_csv_export_writes_tags_joined_with_delimiter(mock_kee, tmp_path):
    """csv_export should join an entry's tags into the groups column"""
    entry = Mock()
    entry.title = "TEST"
    entry.notes = "Test Server"
    entry.username = "user"
    entry.password = "pass"
    entry.tags = ["admin", "power-users"]
    entry.custom_properties = []
    entry.get_custom_property = Mock(return_value="")
    mock_kee.grp_servers.entries = [entry]

    servers = Servers(mock_kee)

    output_file = tmp_path / "export.csv"
    result = servers.csv_export(output_file)

    assert result is True
    content = output_file.read_text(encoding="utf-8-sig")
    assert "admin" in content
    assert "power-users" in content


@pytest.mark.unit
def test_find_entry_by_uuid_raises_if_db_not_opened(mock_kee):
    """find_entry_by_uuid should raise RuntimeError if the db isn't opened"""
    servers = Servers(mock_kee)
    mock_kee.db = None

    with pytest.raises(RuntimeError, match="Database not opened"):
        servers.find_entry_by_uuid("12345678-1234-1234-1234-123456789012")


@pytest.mark.unit
def test_find_entry_by_uuid_raises_if_no_group_selected(mock_kee):
    """find_entry_by_uuid should raise ValueError if no group is selected"""
    servers = Servers(mock_kee)
    servers.kee_grp = None

    with pytest.raises(ValueError, match="No KeePass group selected"):
        servers.find_entry_by_uuid("12345678-1234-1234-1234-123456789012")


@pytest.mark.unit
def test_find_entry_by_uuid_returns_entry(mock_kee):
    """find_entry_by_uuid should return the matching Entry"""
    from pykeepass.entry import Entry

    servers = Servers(mock_kee)

    found_entry = Mock(spec=Entry)
    mock_kee.db.find_entries = Mock(return_value=found_entry)

    result = servers.find_entry_by_uuid("12345678-1234-1234-1234-123456789012")

    assert result is found_entry


@pytest.mark.unit
def test_find_entry_by_uuid_returns_none_if_not_found(mock_kee):
    """find_entry_by_uuid should return None if nothing matches"""
    servers = Servers(mock_kee)
    mock_kee.db.find_entries = Mock(return_value=None)

    result = servers.find_entry_by_uuid("12345678-1234-1234-1234-123456789012")

    assert result is None


# ================================================================================
# Server.reload / save / delete
# ================================================================================


@pytest.mark.unit
def test_server_reload_updates_from_entry(mock_kee):
    """reload should re-fetch the entry and update the Server's attributes"""
    from pykeepass.entry import Entry
    from Pytre_Core.servers import Server

    servers = Servers(mock_kee)

    entry = Mock(spec=Entry)
    entry.uuid = "12345678-1234-1234-1234-123456789012"
    entry.title = "RELOADED"
    entry.notes = "desc"
    entry.username = "user"
    entry.password = "pwd"
    entry.tags = []
    entry.custom_properties = []
    entry.get_custom_property = Mock(return_value=None)

    server = Server(servers)
    server.uuid = "12345678-1234-1234-1234-123456789012"

    mock_kee.db.find_entries = Mock(return_value=entry)

    server.reload()

    assert server.id == "RELOADED"


@pytest.mark.unit
def test_server_reload_does_nothing_if_entry_not_found(mock_kee):
    """reload should leave the Server unchanged if the entry can't be found"""
    from Pytre_Core.servers import Server

    servers = Servers(mock_kee)
    server = Server(servers)
    server.id = "UNCHANGED"
    server.uuid = "12345678-1234-1234-1234-123456789012"

    mock_kee.db.find_entries = Mock(return_value=None)

    server.reload()

    assert server.id == "UNCHANGED"


@pytest.mark.unit
def test_server_save_requires_id(mock_kee):
    """save should raise ValueError if the server has no id"""
    from Pytre_Core.servers import Server

    servers = Servers(mock_kee)
    server = Server(servers)

    with pytest.raises(ValueError, match="must have an id"):
        server.save()


@pytest.mark.unit
def test_server_save_rejects_duplicate_id(mock_kee):
    """save should raise ValueError if another server already uses the same id"""
    from Pytre_Core.servers import Server

    existing_entry = Mock()
    existing_entry.uuid = "existing-uuid"
    existing_entry.title = "DUPLICATE"
    existing_entry.notes = ""
    existing_entry.username = ""
    existing_entry.password = ""
    existing_entry.tags = []
    existing_entry.custom_properties = []
    existing_entry.get_custom_property = Mock(return_value=None)
    mock_kee.grp_servers.entries = [existing_entry]

    servers = Servers(mock_kee)
    server = Server(servers)
    server.id = "DUPLICATE"

    with pytest.raises(ValueError, match="est déjà utilisé"):
        server.save()


@pytest.mark.unit
def test_server_save_rejects_non_numeric_timeout(mock_kee):
    """save should raise ValueError if timeout isn't numeric"""
    from Pytre_Core.servers import Server

    servers = Servers(mock_kee)
    server = Server(servers)
    server.id = "NEW_SERVER"
    server.timeout = "not_a_number"  # type: ignore

    with pytest.raises(ValueError, match="timeout doit être un nombre"):
        server.save()


@pytest.mark.unit
def test_server_save_rejects_non_numeric_login_timeout(mock_kee):
    """save should raise ValueError if login_timeout isn't numeric"""
    from Pytre_Core.servers import Server

    servers = Servers(mock_kee)
    server = Server(servers)
    server.id = "NEW_SERVER"
    server.login_timeout = "not_a_number"  # type: ignore

    with pytest.raises(ValueError, match="login_timeout doit être un nombre"):
        server.save()


@pytest.mark.unit
def test_server_save_raises_if_db_not_opened(mock_kee):
    """save should raise RuntimeError if the KeePass db isn't opened"""
    from Pytre_Core.servers import Server

    servers = Servers(mock_kee)
    server = Server(servers)
    server.id = "NEW_SERVER"
    mock_kee.db = None

    with pytest.raises(RuntimeError, match="Database not opened"):
        server.save()


@pytest.mark.unit
def test_server_save_creates_new_entry(mock_kee):
    """save should create a new KeePass entry when the server has no uuid yet"""
    from Pytre_Core.servers import Server

    servers = Servers(mock_kee)
    server = Server(servers)
    server.id = "NEW_SERVER"

    new_entry = Mock()
    new_entry.uuid = "new-uuid"
    mock_kee.db.add_entry = Mock(return_value=new_entry)

    result = server.save()

    assert result is True
    assert mock_kee.db.add_entry.called
    assert new_entry.title == "NEW_SERVER"
    assert mock_kee.save_db.called


@pytest.mark.unit
def test_server_delete_removes_entry(mock_kee):
    """delete should find the entry by uuid and delete it"""
    from pykeepass.entry import Entry
    from Pytre_Core.servers import Server

    servers = Servers(mock_kee)
    server = Server(servers)
    server.uuid = "12345678-1234-1234-1234-123456789012"

    entry = Mock(spec=Entry)
    mock_kee.db.find_entries = Mock(return_value=entry)

    result = server.delete()

    assert result is True
    assert entry.delete.called
    assert mock_kee.save_db.called


@pytest.mark.unit
def test_server_delete_raises_if_not_found(mock_kee):
    """delete should raise LookupError if the entry can't be found"""
    from Pytre_Core.servers import Server

    servers = Servers(mock_kee)
    server = Server(servers)
    server.uuid = "12345678-1234-1234-1234-123456789012"

    mock_kee.db.find_entries = Mock(return_value=None)

    with pytest.raises(LookupError, match="Server not found"):
        server.delete()


@pytest.mark.unit
def test_server_delete_raises_if_db_not_opened(mock_kee):
    """delete should raise RuntimeError if the KeePass db isn't opened"""
    from Pytre_Core.servers import Server

    servers = Servers(mock_kee)
    server = Server(servers)
    mock_kee.db = None

    with pytest.raises(RuntimeError, match="Database not opened"):
        server.delete()


@pytest.mark.unit
def test_server_delete_raises_on_unexpected_type(mock_kee):
    """delete should raise TypeError if find_entries returns something other than an Entry"""
    from Pytre_Core.servers import Server

    servers = Servers(mock_kee)
    server = Server(servers)
    server.uuid = "12345678-1234-1234-1234-123456789012"

    mock_kee.db.find_entries = Mock(return_value="not-an-entry")

    with pytest.raises(TypeError, match="Unexpected type"):
        server.delete()


@pytest.mark.unit
def test_server_save_updates_existing_entry(mock_kee):
    """save should update the matching entry when the server already has a uuid"""
    from pykeepass.entry import Entry
    from Pytre_Core.servers import Server

    servers = Servers(mock_kee)
    server = Server(servers)
    server.id = "EXISTING"
    server.uuid = "12345678-1234-1234-1234-123456789012"

    existing_entry = Mock(spec=Entry)
    mock_kee.db.find_entries = Mock(return_value=existing_entry)

    result = server.save()

    assert result is True
    assert existing_entry.title == "EXISTING"
    assert not mock_kee.db.add_entry.called
    assert mock_kee.save_db.called


@pytest.mark.unit
def test_server_save_raises_on_unexpected_type(mock_kee):
    """save should raise TypeError if find_entries returns something other than an Entry"""
    from Pytre_Core.servers import Server

    servers = Servers(mock_kee)
    server = Server(servers)
    server.id = "NEW_SERVER"
    server.uuid = "12345678-1234-1234-1234-123456789012"

    mock_kee.db.find_entries = Mock(return_value="not-an-entry")

    with pytest.raises(TypeError, match="Unexpected type"):
        server.save()


# ================================================================================
# Server._load_from_entry Details
# ================================================================================


@pytest.mark.unit
def test_load_from_entry_parses_custom_properties(mock_kee):
    """_load_from_entry should convert login_timeout/timeout to int and uppercase charset"""
    entry = Mock()
    entry.uuid = "uuid-1"
    entry.title = "SERVER1"
    entry.username = "user"
    entry.password = "pass"
    entry.notes = ""
    entry.tags = []
    entry.custom_properties = ["charset", "login_timeout", "timeout", "database"]

    def get_prop(name):
        return {"charset": "utf8", "login_timeout": "5", "timeout": "30", "database": "mydb"}.get(name)

    entry.get_custom_property = Mock(side_effect=get_prop)

    mock_kee.grp_servers.entries = [entry]
    servers = Servers(mock_kee)

    server = servers.servers_dict["SERVER1"]
    assert server.charset == "UTF8"
    assert server.login_timeout == 5
    assert server.timeout == 30
    assert server.database == "mydb"


@pytest.mark.unit
def test_load_from_entry_invalid_timeout_keeps_default(mock_kee):
    """_load_from_entry should keep the default timeout if the stored value isn't numeric"""
    entry = Mock()
    entry.uuid = "uuid-1"
    entry.title = "SERVER1"
    entry.username = "user"
    entry.password = "pass"
    entry.notes = ""
    entry.tags = []
    entry.custom_properties = ["timeout"]
    entry.get_custom_property = Mock(return_value="not_a_number")

    mock_kee.grp_servers.entries = [entry]
    servers = Servers(mock_kee)

    server = servers.servers_dict["SERVER1"]
    assert server.timeout == 300  # default kept


@pytest.mark.unit
def test_load_from_entry_defaults_charset_for_mssql(mock_kee):
    """_load_from_entry should default charset to UTF-8 for mssql servers without one set"""
    entry = Mock()
    entry.uuid = "uuid-1"
    entry.title = "SERVER1"
    entry.username = "user"
    entry.password = "pass"
    entry.notes = ""
    entry.tags = []
    entry.custom_properties = ["type"]
    entry.get_custom_property = Mock(side_effect=lambda key: "mssql" if key == "type" else None)

    mock_kee.grp_servers.entries = [entry]
    servers = Servers(mock_kee)

    server = servers.servers_dict["SERVER1"]
    assert server.charset == "UTF-8"


@pytest.mark.unit
def test_load_from_entry_defaults_charset_for_postgre(mock_kee):
    """_load_from_entry should default charset to UTF8 for postgre servers without one set"""
    entry = Mock()
    entry.uuid = "uuid-1"
    entry.title = "SERVER1"
    entry.username = "user"
    entry.password = "pass"
    entry.notes = ""
    entry.tags = []
    entry.custom_properties = ["type"]
    entry.get_custom_property = Mock(side_effect=lambda key: "postgre" if key == "type" else None)

    mock_kee.grp_servers.entries = [entry]
    servers = Servers(mock_kee)

    server = servers.servers_dict["SERVER1"]
    assert server.charset == "UTF8"


