"""
Tests for Pytre/users.py

REGRESSION TESTS:
- Issue 2.1: CSV import validation (delimiter, quotechar, file existence)
- Issue 4.1: Optimized list search with generators (csv_import)
"""

import pytest
import csv
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from Pytre_Core.users import Users, User


# ================================================================================
# Fixtures
# ================================================================================


@pytest.fixture
def mock_kee():
    """Mock Kee object for Users"""
    mock = Mock()
    mock.is_ko = False
    mock.db = Mock()
    mock.grp_users = Mock()
    mock.grp_users.entries = []
    mock.grp_servers = Mock()
    mock.grp_servers.entries = []
    mock._open_db = Mock(return_value=True)
    mock.save_db = Mock()
    return mock


@pytest.fixture
def sample_csv_users(tmp_path):
    """Create sample CSV file for testing"""
    csv_file = tmp_path / "users.csv"
    content = """id;description;admin;groups;login message
admin_user;Admin User;1;admin;Welcome Admin
jdoe;John Doe;0;users;Welcome John
"""

    csv_file.write_text(content, encoding="latin-1")
    return csv_file


# ================================================================================
# REGRESSION TESTS - Issue 2.1: CSV Import Validation
# ================================================================================


@pytest.mark.regression
def test_csv_import_validates_delimiter(mock_kee):
    """
    Regression test for Issue 2.1 - delimiter must be valid

    Delimiter must be single character and different from quotechar.
    """
    users = Users(mock_kee)

    # Multiple characters
    with pytest.raises(ValueError, match="single character"):
        users.csv_import("test.csv", delimiter=";;")

    # Same as quotechar
    with pytest.raises(ValueError, match="different from quote"):
        users.csv_import("test.csv", delimiter='"', quotechar='"')


@pytest.mark.regression
def test_csv_import_validates_quotechar(mock_kee):
    """Quotechar must be single character"""
    users = Users(mock_kee)

    with pytest.raises(ValueError, match="single character"):
        users.csv_import("test.csv", delimiter=";", quotechar='""')


@pytest.mark.regression
def test_csv_import_validates_file_exists(mock_kee):
    """CSV import should validate file existence"""
    users = Users(mock_kee)

    with pytest.raises(FileNotFoundError, match="does not exist"):
        users.csv_import("/nonexistent/users.csv")


@pytest.mark.regression
def test_csv_import_validates_required_columns(tmp_path, mock_kee):
    """CSV import should validate required columns exist"""
    users = Users(mock_kee)

    # Create CSV with missing columns
    csv_file = tmp_path / "incomplete.csv"
    content = "id;description\nTEST;Test User"
    csv_file.write_text(content, encoding="latin-1")

    with pytest.raises(ValueError, match="Missing columns"):
        users.csv_import(csv_file)


# ================================================================================
# REGRESSION TESTS - Issue 4.1: Optimized Search
# ================================================================================


@pytest.mark.regression
def test_csv_import_uses_generator_for_search(tmp_path, mock_kee):
    """
    Regression test for Issue 4.1 - use generator with next()

    The search for existing entry uses next() with generator
    expression for early termination.
    """

    # Create existing entries
    existing_entry = Mock()
    existing_entry.title = "EXISTING_USER"
    existing_entry.username = "old_user"
    existing_entry.password = "old_pass"
    existing_entry.notes = "Doe"
    existing_entry.tags = []
    existing_entry.custom_properties = []
    existing_entry.set_custom_property = Mock()

    existing_entry.username = "old_user"  # matched case-insensitively against csv_row["id"]
    mock_kee.grp_users.entries = [existing_entry]

    users = Users(mock_kee)

    # Create CSV with entry that matches existing (case-insensitive)
    csv_file = tmp_path / "update.csv"
    headers = ["id", "description", "admin", "groups", "login message"]
    row = ["OLD_USER", "Updated Name", "0", "users", "Welcome"]

    with open(csv_file, "w", encoding="latin-1", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, delimiter=";")
        writer.writeheader()
        writer.writerow(dict(zip(headers, row)))

    # Import should update existing entry (not create new one)
    users.csv_import(csv_file)

    # Verify: Existing entry was updated
    assert existing_entry.title == "Updated Name"

    # Verify: No new entry was created (add_entry not called)
    assert not mock_kee.db.add_entry.called


# ================================================================================
# CSV Import Functionality
# ================================================================================


@pytest.mark.unit
def test_csv_import_creates_new_entries(tmp_path, mock_kee):
    """CSV import should create new entries if they don't exist"""
    users = Users(mock_kee)

    # Mock add_entry to return new entry
    new_entry = Mock()
    new_entry.title = ""
    new_entry.username = ""
    new_entry.password = ""
    new_entry.notes = ""
    new_entry.tags = []
    new_entry.set_custom_property = Mock()

    mock_kee.db.add_entry = Mock(return_value=new_entry)

    # Create CSV with new user
    csv_file = tmp_path / "new.csv"
    headers = ["id", "description", "admin", "groups", "login message"]
    row = ["newuser", "New User", "0", "users", "Welcome"]

    with open(csv_file, "w", encoding="latin-1", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, delimiter=";")
        writer.writeheader()
        writer.writerow(dict(zip(headers, row)))

    # Import
    users.csv_import(csv_file)

    # Verify: add_entry was called
    assert mock_kee.db.add_entry.called

    # Verify: Entry properties were set (description becomes title)
    assert new_entry.title == "New User"


@pytest.mark.unit
def test_csv_import_parses_groups_with_delimiter(tmp_path, mock_kee):
    """CSV import should correctly parse groups containing delimiter"""
    users = Users(mock_kee)

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
    headers = ["id", "description", "admin", "groups", "login message"]
    # Groups with delimiter in quotes
    row = ["user1", "User One", "0", '"admin;users;power-users"', "Welcome"]

    with open(csv_file, "w", encoding="latin-1", newline="") as f:
        writer = csv.writer(f, delimiter=";", quotechar='"')
        writer.writerow(headers)
        writer.writerow(row)

    # Import
    users.csv_import(csv_file)

    # Verify: Groups were split correctly
    # Semicolons in groups should be replaced with underscores
    assert "admin_users_power-users" in new_entry.tags or len(new_entry.tags) > 1


@pytest.mark.unit
def test_csv_import_replaces_invalid_chars_in_tags(tmp_path, mock_kee):
    """CSV import should replace invalid characters in tags"""
    users = Users(mock_kee)

    new_entry = Mock()
    new_entry.title = ""
    new_entry.username = ""
    new_entry.password = ""
    new_entry.notes = ""
    new_entry.tags = []
    new_entry.set_custom_property = Mock()
    mock_kee.db.add_entry = Mock(return_value=new_entry)

    csv_file = tmp_path / "tags.csv"
    headers = ["id", "description", "admin", "groups", "login message"]
    row = ["user1", "User One", "0", "admin,users;power", "Welcome"]

    with open(csv_file, "w", encoding="latin-1", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, delimiter=";")
        writer.writeheader()
        writer.writerow(dict(zip(headers, row)))

    users.csv_import(csv_file)

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
    users = Users(mock_kee)

    output_file = tmp_path / "export.csv"

    with pytest.raises(ValueError, match="single character"):
        users.csv_export(output_file, delimiter=";;")


@pytest.mark.unit
def test_csv_export_validates_file_not_exists_without_overwrite(mock_kee, tmp_path):
    """CSV export should not overwrite existing file by default"""
    users = Users(mock_kee)

    # Create existing file
    existing_file = tmp_path / "existing.csv"
    existing_file.write_text("existing data")

    with pytest.raises(FileExistsError, match="already exists"):
        users.csv_export(existing_file, overwrite=False)


@pytest.mark.unit
def test_csv_export_allows_overwrite_when_flag_set(mock_kee, tmp_path):
    """CSV export should allow overwrite when flag is True"""

    # Add mock entry
    entry = Mock()
    entry.title = "TEST"
    entry.notes = "Doe"
    entry.username = "John"
    entry.password = "pass"
    entry.tags = []
    entry.custom_properties = []
    entry.get_custom_property = Mock(return_value="")
    mock_kee.grp_users.entries = [entry]

    users = Users(mock_kee)

    # Create existing file
    existing_file = tmp_path / "existing.csv"
    existing_file.write_text("old data")

    # Should not raise error
    result = users.csv_export(existing_file, overwrite=True)

    assert result is True
    # File should be overwritten
    assert "old data" not in existing_file.read_text()


# ================================================================================
# Get All Users Functionality
# ================================================================================


@pytest.mark.unit
def test_get_all_users_returns_dict(mock_kee):
    """get_all_users should return dict of User objects"""
    # Add mock entries
    entry1 = Mock()
    entry1.uuid = "uuid-1"
    entry1.title = "USER1"
    entry1.username = "John"
    entry1.password = "pass1"
    entry1.notes = "Doe"
    entry1.tags = ["admin"]
    entry1.custom_properties = []

    entry2 = Mock()
    entry2.uuid = "uuid-2"
    entry2.title = "USER2"
    entry2.username = "Jane"
    entry2.password = "pass2"
    entry2.notes = "Smith"
    entry2.tags = ["users"]
    entry2.custom_properties = []

    mock_kee.grp_users.entries = [entry1, entry2]

    users = Users(mock_kee)

    result = users.get_all_users()

    assert isinstance(result, list)
    assert len(result) == 2
    usernames = [u.username for u in result]
    assert "John" in usernames
    assert "Jane" in usernames


@pytest.mark.unit
def test_get_all_users_populates_custom_attribs(mock_kee):
    """get_all_users should collect custom attribute names across all entries"""
    entry1 = Mock()
    entry1.uuid = "uuid-1"
    entry1.title = "USER1"
    entry1.username = "John"
    entry1.password = "pass1"
    entry1.notes = "Doe"
    entry1.tags = []
    entry1.custom_properties = ["service"]
    entry1.get_custom_property = Mock(return_value="IT")

    mock_kee.grp_users.entries = [entry1]

    users = Users(mock_kee)
    users.get_all_users()

    assert "service" in users.attribs_cust


# ================================================================================
# User Instances Functionality
# ================================================================================


@pytest.mark.unit
def test_user_instances_are_independent(mock_kee):
    """Each User() call should build its own instance, not share state with others"""
    users = Users(mock_kee)
    user1 = User(users, detect_user=False)
    user2 = User(users, detect_user=False)

    assert user1 is not user2


@pytest.mark.unit
def test_user_sets_properties(mock_kee):
    """User should allow setting properties"""
    users = Users(mock_kee)
    current_user = User(users, detect_user=False)
    current_user.username = "admin"
    current_user.title = "Admin"
    current_user.admin = True
    current_user.grp_authorized = ["admin", "users"]

    assert current_user.username == "admin"
    assert current_user.title == "Admin"
    assert current_user.admin is True
    assert current_user.grp_authorized == ["admin", "users"]


# ================================================================================
# Database Open Functionality
# ================================================================================


@pytest.mark.unit
def test_open_db_returns_false_on_failure(mock_kee):
    """open_db should return False if database fails to open"""
    mock_kee.is_ko = True  # Database failed

    users = Users(mock_kee)
    result = users.open_db()

    assert result is False


@pytest.mark.unit
def test_open_db_returns_true_on_success(mock_kee):
    """open_db should return True if database opens successfully"""
    users = Users(mock_kee)
    result = users.open_db()

    assert result is True


# ================================================================================
# Error Handling
# ================================================================================


@pytest.mark.unit
def test_csv_import_handles_empty_file(tmp_path, mock_kee):
    """CSV import should handle empty file gracefully"""
    users = Users(mock_kee)

    # Create empty CSV
    csv_file = tmp_path / "empty.csv"
    csv_file.write_text("", encoding="latin-1")

    # Should raise error (no headers)
    with pytest.raises(Exception):
        users.csv_import(csv_file)


@pytest.mark.unit
def test_csv_import_handles_malformed_csv(tmp_path, mock_kee):
    """CSV import should handle malformed CSV gracefully"""
    users = Users(mock_kee)

    # Create malformed CSV (missing required columns)
    csv_file = tmp_path / "malformed.csv"
    csv_file.write_text("id;description\nvalue1;desc1\n", encoding="latin-1")

    # Should raise ValueError for missing columns
    with pytest.raises(ValueError, match="Missing columns"):
        users.csv_import(csv_file)


# ================================================================================
# users_admin_exists Functionality
# ================================================================================


@pytest.mark.unit
def test_users_admin_exists_raises_if_no_group_selected(mock_kee):
    """users_admin_exists should raise ValueError if no group is selected"""
    users = Users(mock_kee)
    users.kee_grp = None

    with pytest.raises(ValueError, match="No KeePass group selected"):
        users.users_admin_exists()


@pytest.mark.unit
def test_users_admin_exists_returns_true_when_superuser_present(mock_kee):
    """users_admin_exists should return True if any entry has superuser=true"""
    admin_entry = Mock()
    admin_entry.tags = []
    admin_entry.get_custom_property = Mock(return_value="true")
    mock_kee.grp_users.entries = [admin_entry]

    users = Users(mock_kee)

    assert users.users_admin_exists() is True


@pytest.mark.unit
def test_users_admin_exists_returns_false_when_no_superuser(mock_kee):
    """users_admin_exists should return False if no entry has superuser=true"""
    regular_entry = Mock()
    regular_entry.tags = []
    regular_entry.get_custom_property = Mock(return_value="false")
    mock_kee.grp_users.entries = [regular_entry]

    users = Users(mock_kee)

    assert users.users_admin_exists() is False


# ================================================================================
# get_cust_attribs_list / _get_cust_attribs_list Functionality
# ================================================================================


@pytest.mark.unit
def test_get_cust_attribs_list_raises_if_no_group_selected(mock_kee):
    """get_cust_attribs_list should raise ValueError if no group is selected"""
    users = Users(mock_kee)
    users.kee_grp = None

    with pytest.raises(ValueError, match="No KeePass group selected"):
        users.get_cust_attribs_list()


@pytest.mark.unit
def test_get_cust_attribs_list_excludes_standard_attribs(mock_kee):
    """get_cust_attribs_list should exclude standard attributes and 'superuser'"""
    entry = Mock()
    entry.tags = []
    entry.custom_properties = ["username", "title", "admin", "msg_login", "superuser", "service"]
    mock_kee.grp_users.entries = [entry]

    users = Users(mock_kee)
    result = users.get_cust_attribs_list()

    assert result == ["service"]


# ================================================================================
# users_add_groups / users_remove_groups Functionality
# ================================================================================


@pytest.mark.unit
def test_users_add_groups_raises_if_no_group_selected(mock_kee):
    """users_add_groups should raise ValueError if no group is selected"""
    users = Users(mock_kee)
    users.kee_grp = None

    with pytest.raises(ValueError, match="No KeePass group selected"):
        users.users_add_groups(["jdoe"], ["admin"])


@pytest.mark.unit
def test_users_add_groups_adds_new_group_and_saves(mock_kee):
    """users_add_groups should add the group to matching users and save"""
    entry = Mock()
    entry.username = "jdoe"
    entry.tags = ["users"]
    mock_kee.grp_users.entries = [entry]

    users = Users(mock_kee)
    users.users_add_groups(["jdoe"], ["admin"])

    assert "admin" in entry.tags
    assert mock_kee.save_db.called


@pytest.mark.unit
def test_users_add_groups_ignores_all_group(mock_kee):
    """users_add_groups should never add the 'all' group explicitly"""
    entry = Mock()
    entry.username = "jdoe"
    entry.tags = ["users"]
    mock_kee.grp_users.entries = [entry]

    users = Users(mock_kee)
    users.users_add_groups(["jdoe"], ["all"])

    assert entry.tags == ["users"]
    assert not mock_kee.save_db.called


@pytest.mark.unit
def test_users_add_groups_skips_non_matching_usernames(mock_kee):
    """users_add_groups should not touch entries whose username isn't targeted"""
    entry = Mock()
    entry.username = "other"
    entry.tags = ["users"]
    mock_kee.grp_users.entries = [entry]

    users = Users(mock_kee)
    users.users_add_groups(["jdoe"], ["admin"])

    assert entry.tags == ["users"]
    assert not mock_kee.save_db.called


@pytest.mark.unit
def test_users_remove_groups_raises_if_no_group_selected(mock_kee):
    """users_remove_groups should raise ValueError if no group is selected"""
    users = Users(mock_kee)
    users.kee_grp = None

    with pytest.raises(ValueError, match="No KeePass group selected"):
        users.users_remove_groups(["jdoe"], ["admin"])


@pytest.mark.unit
def test_users_remove_groups_removes_matching_group_and_saves(mock_kee):
    """users_remove_groups should remove the group from matching users and save"""
    entry = Mock()
    entry.username = "jdoe"
    entry.tags = ["users", "admin"]
    mock_kee.grp_users.entries = [entry]

    users = Users(mock_kee)
    users.users_remove_groups(["jdoe"], ["admin"])

    assert entry.tags == ["users"]
    assert mock_kee.save_db.called


@pytest.mark.unit
def test_users_remove_groups_skips_entries_without_tags(mock_kee):
    """users_remove_groups should skip entries with no tags"""
    entry = Mock()
    entry.username = "jdoe"
    entry.tags = []
    mock_kee.grp_users.entries = [entry]

    users = Users(mock_kee)
    users.users_remove_groups(["jdoe"], ["admin"])

    assert not mock_kee.save_db.called


# ================================================================================
# modify_custom_attribs / remove_custom_attribs Functionality
# ================================================================================


@pytest.mark.unit
def test_modify_custom_attribs_raises_if_no_group_selected(mock_kee):
    """modify_custom_attribs should raise ValueError if no group is selected"""
    users = Users(mock_kee)
    users.kee_grp = None

    with pytest.raises(ValueError, match="No KeePass group selected"):
        users.modify_custom_attribs(["service"])


@pytest.mark.unit
def test_modify_custom_attribs_creates_missing_field(mock_kee):
    """modify_custom_attribs should create the field with an empty default if missing"""
    entry = Mock()
    entry.tags = []
    entry.custom_properties = []
    mock_kee.grp_users.entries = [entry]

    users = Users(mock_kee)
    users.modify_custom_attribs(["service"])

    entry.set_custom_property.assert_called_once_with("service", "")
    assert mock_kee.save_db.called
    assert "service" in users.attribs_cust


@pytest.mark.unit
def test_modify_custom_attribs_updates_existing_field(mock_kee):
    """modify_custom_attribs should update the field if a new_value is given and differs"""
    entry = Mock()
    entry.tags = []
    entry.custom_properties = ["service"]
    entry.get_custom_property = Mock(return_value="HR")
    mock_kee.grp_users.entries = [entry]

    users = Users(mock_kee)
    users.modify_custom_attribs(["service"], new_value="IT")

    entry.set_custom_property.assert_called_once_with("service", "IT")
    assert mock_kee.save_db.called


@pytest.mark.unit
def test_modify_custom_attribs_no_change_does_not_save(mock_kee):
    """modify_custom_attribs should not save if nothing changed"""
    entry = Mock()
    entry.tags = []
    entry.custom_properties = ["service"]
    entry.get_custom_property = Mock(return_value="IT")
    mock_kee.grp_users.entries = [entry]

    users = Users(mock_kee)
    users.modify_custom_attribs(["service"], new_value="IT")

    assert not entry.set_custom_property.called
    assert not mock_kee.save_db.called


@pytest.mark.unit
def test_remove_custom_attribs_raises_if_no_group_selected(mock_kee):
    """remove_custom_attribs should raise ValueError if no group is selected"""
    users = Users(mock_kee)
    users.kee_grp = None

    with pytest.raises(ValueError, match="No KeePass group selected"):
        users.remove_custom_attribs(["service"])


@pytest.mark.unit
def test_remove_custom_attribs_deletes_existing_field(mock_kee):
    """remove_custom_attribs should delete the field and save"""
    entry = Mock()
    entry.tags = []
    entry.custom_properties = ["service"]
    mock_kee.grp_users.entries = [entry]

    users = Users(mock_kee)
    users.attribs_cust = ["service"]
    users.remove_custom_attribs(["service"])

    entry.delete_custom_property.assert_called_once_with("service")
    assert mock_kee.save_db.called
    assert "service" not in users.attribs_cust


@pytest.mark.unit
def test_remove_custom_attribs_skips_missing_field(mock_kee):
    """remove_custom_attribs should not touch entries that don't have the field"""
    entry = Mock()
    entry.tags = []
    entry.custom_properties = []
    mock_kee.grp_users.entries = [entry]

    users = Users(mock_kee)
    users.remove_custom_attribs(["service"])

    assert not entry.delete_custom_property.called
    assert not mock_kee.save_db.called


# ================================================================================
# find_entry_by_username / find_user_by_uuid Functionality
# ================================================================================


@pytest.mark.unit
def test_find_entry_by_username_raises_if_no_group_selected(mock_kee):
    """find_entry_by_username should raise ValueError if no group is selected"""
    users = Users(mock_kee)
    users.kee_grp = None

    with pytest.raises(ValueError, match="No KeePass group selected"):
        users.find_entry_by_username("jdoe")


@pytest.mark.unit
def test_find_entry_by_username_matches_case_insensitively(mock_kee):
    """find_entry_by_username should match usernames case-insensitively"""
    entry = Mock()
    entry.username = "JDoe"
    entry.tags = []
    mock_kee.grp_users.entries = [entry]

    users = Users(mock_kee)
    result = users.find_entry_by_username("jdoe")

    assert result is entry


@pytest.mark.unit
def test_find_entry_by_username_returns_none_if_not_found(mock_kee):
    """find_entry_by_username should return None if no entry matches"""
    entry = Mock()
    entry.username = "other"
    entry.tags = []
    mock_kee.grp_users.entries = [entry]

    users = Users(mock_kee)
    result = users.find_entry_by_username("jdoe")

    assert result is None


@pytest.mark.unit
def test_find_user_by_uuid_raises_if_db_not_opened(mock_kee):
    """find_user_by_uuid should raise RuntimeError if the db isn't opened"""
    users = Users(mock_kee)
    mock_kee.db = None

    with pytest.raises(RuntimeError, match="Database not opened"):
        users.find_user_by_uuid("12345678-1234-1234-1234-123456789012")


@pytest.mark.unit
def test_find_user_by_uuid_returns_user(mock_kee):
    """find_user_by_uuid should return a User built from the matching entry"""
    from pykeepass.entry import Entry
    from Pytre_Core.users import User

    entry = Mock(spec=Entry)
    entry.uuid = "12345678-1234-1234-1234-123456789012"
    entry.username = "jdoe"
    entry.title = ""
    entry.tags = []
    entry.custom_properties = []
    mock_kee.db.find_entries = Mock(return_value=entry)

    users = Users(mock_kee)
    result = users.find_user_by_uuid("12345678-1234-1234-1234-123456789012")

    assert isinstance(result, User)
    assert result.username == "jdoe"


@pytest.mark.unit
def test_find_user_by_uuid_returns_none_if_not_found(mock_kee):
    """find_user_by_uuid should return None if nothing matches"""
    mock_kee.db.find_entries = Mock(return_value=None)

    users = Users(mock_kee)
    result = users.find_user_by_uuid("12345678-1234-1234-1234-123456789012")

    assert result is None


@pytest.mark.unit
def test_get_current_username_uses_getpass(mock_kee, monkeypatch):
    """get_current_username should return the OS username, prefixed by domain if set"""
    users = Users(mock_kee)

    monkeypatch.setattr("Pytre_Core.users.getpass.getuser", lambda: "jdoe")
    monkeypatch.delenv("userdnsdomain", raising=False)

    assert users.get_current_username() == "jdoe"


@pytest.mark.unit
def test_get_current_username_prefixes_domain(mock_kee, monkeypatch):
    """get_current_username should prefix with the domain when set"""
    users = Users(mock_kee)

    monkeypatch.setattr("Pytre_Core.users.getpass.getuser", lambda: "jdoe")
    monkeypatch.setenv("userdnsdomain", "CORP")

    assert users.get_current_username() == "CORP\\jdoe"


# ================================================================================
# User Class Tests
# ================================================================================


@pytest.mark.unit
def test_user_load_from_entry_sets_std_attribs(mock_kee):
    """User._load_from_entry should set standard attributes and mark the user authorized"""
    from pykeepass.entry import Entry
    from Pytre_Core.users import User

    entry = Mock(spec=Entry)
    entry.uuid = "12345678-1234-1234-1234-123456789012"
    entry.username = "jdoe"
    entry.title = "John Doe"
    entry.tags = ["admin"]
    entry.custom_properties = ["msg_login", "superuser", "service"]

    def get_prop(name):
        return {"msg_login": "Welcome", "superuser": "true", "service": "IT"}[name]

    entry.get_custom_property = Mock(side_effect=get_prop)

    users = Users(mock_kee)
    user = User(users, entry=entry)

    assert user.exists is True
    assert user.is_authorized is True
    assert user.username == "jdoe"
    assert user.title == "John Doe"
    assert user.admin is True
    assert user.msg_login == "Welcome"
    assert user.attribs_cust == {"service": "IT"}
    assert "admin" in user.grp_authorized


@pytest.mark.unit
def test_user_load_finds_entry_via_users(mock_kee):
    """User.load should look up the entry through Users.find_entry_by_username"""
    from pykeepass.entry import Entry
    from Pytre_Core.users import User

    entry = Mock(spec=Entry)
    entry.uuid = "12345678-1234-1234-1234-123456789012"
    entry.username = "jdoe"
    entry.title = "John Doe"
    entry.tags = []
    entry.custom_properties = []
    mock_kee.grp_users.entries = [entry]

    users = Users(mock_kee)
    user = User(users, username="jdoe")

    assert user.exists is True
    assert user.title == "John Doe"


@pytest.mark.unit
def test_user_load_does_nothing_if_db_fails_to_open(mock_kee):
    """User.load should leave the user unauthorized if the db can't be opened"""
    from Pytre_Core.users import User

    mock_kee.is_ko = True
    users = Users(mock_kee)

    user = User(users, username="jdoe")

    assert user.exists is False
    assert user.is_authorized is False


@pytest.mark.unit
def test_user_save_requires_group_selected(mock_kee):
    """save should raise ValueError if no KeePass group is selected"""
    from Pytre_Core.users import User

    users = Users(mock_kee)
    users.kee_grp = None
    user = User(users, detect_user=False)
    user.username = "jdoe"

    with pytest.raises(ValueError, match="No KeePass group selected"):
        user.save()


@pytest.mark.unit
def test_user_save_requires_username(mock_kee):
    """save should raise ValueError if the user has no username"""
    from Pytre_Core.users import User

    users = Users(mock_kee)
    user = User(users, detect_user=False)

    with pytest.raises(ValueError, match="must have an Id"):
        user.save()


@pytest.mark.unit
def test_user_save_rejects_duplicate_username(mock_kee):
    """save should raise ValueError if another user already uses the same username"""
    from Pytre_Core.users import User

    existing_entry = Mock()
    existing_entry.uuid = "existing-uuid"
    existing_entry.username = "jdoe"
    existing_entry.tags = []
    mock_kee.grp_users.entries = [existing_entry]

    users = Users(mock_kee)
    user = User(users, detect_user=False)
    user.username = "jdoe"

    with pytest.raises(ValueError, match="est déjà utilisé"):
        user.save()


@pytest.mark.unit
def test_user_save_raises_if_db_not_opened(mock_kee):
    """save should raise RuntimeError if the KeePass db isn't opened"""
    from Pytre_Core.users import User

    users = Users(mock_kee)
    user = User(users, detect_user=False)
    user.username = "jdoe"
    mock_kee.db = None

    with pytest.raises(RuntimeError, match="Database not opened"):
        user.save()


@pytest.mark.unit
def test_user_save_creates_new_entry(mock_kee):
    """save should create a new KeePass entry when the user has no uuid yet"""
    from Pytre_Core.users import User

    users = Users(mock_kee)
    user = User(users, detect_user=False)
    user.username = "jdoe"
    user.title = "John Doe"

    new_entry = Mock()
    new_entry.uuid = "new-uuid"
    mock_kee.db.add_entry = Mock(return_value=new_entry)

    result = user.save()

    assert result is True
    assert mock_kee.db.add_entry.called
    assert new_entry.username == "jdoe"
    assert new_entry.title == "John Doe"
    assert mock_kee.save_db.called
    assert user.exists is True
    assert user.is_authorized is True


@pytest.mark.unit
def test_user_save_raises_on_unexpected_type(mock_kee):
    """save should raise TypeError if find_entries returns something other than an Entry"""
    from Pytre_Core.users import User

    users = Users(mock_kee)
    user = User(users, detect_user=False)
    user.username = "jdoe"
    user.uuid = "12345678-1234-1234-1234-123456789012"

    mock_kee.db.find_entries = Mock(return_value="not-an-entry")

    with pytest.raises(TypeError, match="Unexpected type"):
        user.save()


@pytest.mark.unit
def test_user_delete_raises_if_db_not_opened(mock_kee):
    """delete should raise RuntimeError if the KeePass db isn't opened"""
    from Pytre_Core.users import User

    users = Users(mock_kee)
    user = User(users, detect_user=False)
    mock_kee.db = None

    with pytest.raises(RuntimeError, match="Database not opened"):
        user.delete()


@pytest.mark.unit
def test_user_delete_raises_if_not_found(mock_kee):
    """delete should raise LookupError if the entry can't be found"""
    from Pytre_Core.users import User

    users = Users(mock_kee)
    user = User(users, detect_user=False)
    user.uuid = "12345678-1234-1234-1234-123456789012"

    mock_kee.db.find_entries = Mock(return_value=None)

    with pytest.raises(LookupError, match="User not found"):
        user.delete()


@pytest.mark.unit
def test_user_delete_removes_entry(mock_kee):
    """delete should find the entry by uuid and delete it"""
    from pykeepass.entry import Entry
    from Pytre_Core.users import User

    users = Users(mock_kee)
    user = User(users, detect_user=False)
    user.uuid = "12345678-1234-1234-1234-123456789012"

    entry = Mock(spec=Entry)
    mock_kee.db.find_entries = Mock(return_value=entry)

    result = user.delete()

    assert result is True
    assert entry.delete.called
    assert mock_kee.save_db.called


# ================================================================================
# Remaining edge cases
# ================================================================================


@pytest.mark.unit
def test_get_all_users_raises_if_no_group_selected(mock_kee):
    """get_all_users should raise ValueError if no group is selected"""
    users = Users(mock_kee)
    users.kee_grp = None

    with pytest.raises(ValueError, match="No KeePass group selected"):
        users.get_all_users()


@pytest.mark.unit
def test_csv_import_raises_if_db_not_opened(tmp_path, mock_kee):
    """csv_import should raise RuntimeError if the KeePass db failed to open"""
    users = Users(mock_kee)
    mock_kee.db = None

    csv_file = tmp_path / "users.csv"
    csv_file.write_text("id;description;admin;groups;login message\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Database not opened"):
        users.csv_import(csv_file)


@pytest.mark.unit
def test_csv_import_raises_if_encoding_not_detected(tmp_path, mock_kee):
    """csv_import should raise ValueError if the file's encoding can't be detected"""
    users = Users(mock_kee)

    csv_file = tmp_path / "users.csv"
    csv_file.write_text("id;description;admin;groups;login message\n", encoding="utf-8")

    with patch("Pytre_Core.users.detect_encoding", return_value=None):
        with pytest.raises(ValueError, match="Unable to detect encoding"):
            users.csv_import(csv_file)


@pytest.mark.unit
def test_csv_import_skips_row_with_empty_id(tmp_path, mock_kee):
    """csv_import should skip rows with no id, without crashing"""
    users = Users(mock_kee)

    csv_file = tmp_path / "users.csv"
    content = "id;description;admin;groups;login message\n;No Id;0;;Welcome\n"
    csv_file.write_text(content, encoding="latin-1")

    result = users.csv_import(csv_file)

    assert result is True
    assert not mock_kee.db.add_entry.called


@pytest.mark.unit
def test_csv_import_writes_custom_columns(tmp_path, mock_kee):
    """csv_import should write custom attribute columns present in the file"""
    existing_entry = Mock()
    existing_entry.username = ""
    existing_entry.tags = []
    existing_entry.custom_properties = ["service"]
    mock_kee.grp_users.entries = [existing_entry]

    users = Users(mock_kee)

    new_entry = Mock()
    new_entry.set_custom_property = Mock()
    mock_kee.db.add_entry = Mock(return_value=new_entry)

    csv_file = tmp_path / "users.csv"
    content = "id;description;admin;groups;login message;service\nnewuser;New User;0;;Welcome;IT\n"
    csv_file.write_text(content, encoding="latin-1")

    users.csv_import(csv_file)

    new_entry.set_custom_property.assert_any_call("service", "IT")


@pytest.mark.unit
def test_csv_export_writes_tags_and_custom_columns(mock_kee, tmp_path):
    """csv_export should write groups joined with the delimiter and custom columns"""
    entry = Mock()
    entry.username = "jdoe"
    entry.title = "John Doe"
    entry.tags = ["admin", "users"]
    entry.custom_properties = ["service"]

    def get_prop(name):
        return {"superuser": "true", "msg_login": "Welcome", "service": "IT"}.get(name, "")

    entry.get_custom_property = Mock(side_effect=get_prop)
    mock_kee.grp_users.entries = [entry]

    users = Users(mock_kee)

    output_file = tmp_path / "export.csv"
    result = users.csv_export(output_file)

    assert result is True
    content = output_file.read_text(encoding="utf-8-sig")
    assert "jdoe" in content
    assert "admin" in content
    assert "IT" in content
    # superuser=true should be exported as 1
    assert ";1;" in content


# ================================================================================
# User.to_dict / __repr__ / __str__
# ================================================================================


@pytest.mark.unit
def test_user_to_dict_includes_custom_attribs(mock_kee):
    """to_dict should include uuid/username/etc plus any custom attributes"""
    from Pytre_Core.users import User

    users = Users(mock_kee)
    user = User(users, detect_user=False)
    user.username = "jdoe"
    user.title = "John Doe"
    user.attribs_cust = {"service": "IT"}

    result = user.to_dict()

    assert result["id"] == "jdoe"
    assert result["title"] == "John Doe"
    assert result["service"] == "IT"


@pytest.mark.unit
def test_user_repr_and_str(mock_kee):
    """__repr__ and __str__ should both return the to_dict() string"""
    from Pytre_Core.users import User

    users = Users(mock_kee)
    user = User(users, detect_user=False)
    user.username = "jdoe"

    assert repr(user) == str(user.to_dict())
    assert str(user) == str(user.to_dict())


@pytest.mark.unit
def test_user_load_from_entry_returns_false_for_falsy_entry(mock_kee):
    """_load_from_entry should return False and do nothing for a falsy entry"""
    from Pytre_Core.users import User

    users = Users(mock_kee)
    user = User(users, detect_user=False)

    assert user._load_from_entry(None) is False
    assert user.exists is False


@pytest.mark.unit
def test_user_save_writes_custom_attribs(mock_kee):
    """save should persist attribs_cust as custom properties on the entry"""
    from Pytre_Core.users import User

    users = Users(mock_kee)
    user = User(users, detect_user=False)
    user.username = "jdoe"
    user.attribs_cust = {"service": "IT"}

    new_entry = Mock()
    new_entry.uuid = "new-uuid"
    mock_kee.db.add_entry = Mock(return_value=new_entry)

    user.save()

    new_entry.set_custom_property.assert_any_call("service", "IT")


@pytest.mark.unit
def test_csv_import_raises_if_no_group_selected(tmp_path, mock_kee):
    """csv_import should raise ValueError if no KeePass group is selected"""
    users = Users(mock_kee)
    users.kee_grp = None

    csv_file = tmp_path / "users.csv"
    csv_file.write_text("id;description;admin;groups;login message\n", encoding="utf-8")

    with pytest.raises(ValueError, match="No KeePass group selected"):
        users.csv_import(csv_file)


@pytest.mark.unit
def test_csv_import_sets_superuser_true_for_admin_flag(tmp_path, mock_kee):
    """csv_import should set superuser=true when the admin column is '1'"""
    users = Users(mock_kee)

    new_entry = Mock()
    new_entry.set_custom_property = Mock()
    mock_kee.db.add_entry = Mock(return_value=new_entry)

    csv_file = tmp_path / "users.csv"
    content = "id;description;admin;groups;login message\nadminuser;Admin;1;;Welcome\n"
    csv_file.write_text(content, encoding="latin-1")

    users.csv_import(csv_file)

    new_entry.set_custom_property.assert_any_call("superuser", "true")


@pytest.mark.unit
def test_csv_export_raises_if_no_group_selected(mock_kee, tmp_path):
    """csv_export should raise ValueError if no KeePass group is selected"""
    users = Users(mock_kee)
    users.kee_grp = None

    output_file = tmp_path / "export.csv"

    with pytest.raises(ValueError, match="No KeePass group selected"):
        users.csv_export(output_file)


@pytest.mark.unit
def test_user_delete_raises_on_unexpected_type(mock_kee):
    """delete should raise TypeError if find_entries returns something other than an Entry"""
    from Pytre_Core.users import User

    users = Users(mock_kee)
    user = User(users, detect_user=False)
    user.uuid = "12345678-1234-1234-1234-123456789012"

    mock_kee.db.find_entries = Mock(return_value="not-an-entry")

    with pytest.raises(TypeError, match="Unexpected type"):
        user.delete()
