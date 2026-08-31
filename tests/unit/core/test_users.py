"""
Tests for Pytre/users.py

REGRESSION TESTS:
- CSV import validation (delimiter, quotechar, file existence)
- Optimized list search with generators (csv_import)
"""

import pytest
import csv
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, call, patch, MagicMock
from uuid import UUID

from pykeepass.entry import Entry

from Pytre_Core.users import (
    AuthType,
    Users,
    User,
    _CsvStatus,
    UserField,
    get_dummy_hash,
    hash_password,
    verify_password,
)


def _make_user_entry(uuid_str: str, username: str, tags: list[str]) -> Mock:
    """Minimal Mock(spec=Entry) for tests going through find_user_by_uuid() - only what
    User._load_from_entry()/save() actually touch."""
    entry = Mock(spec=Entry)
    entry.uuid = UUID(uuid_str)
    entry.username = username
    entry.title = ""
    entry.password = ""
    entry.tags = tags
    entry.custom_properties = []
    return entry


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
# hash_password / verify_password / get_dummy_hash
# ================================================================================


@pytest.mark.unit
def test_hash_password_returns_a_verifiable_hash():
    password_hash = hash_password("s3cret")

    assert verify_password("s3cret", password_hash) is True


@pytest.mark.unit
def test_verify_password_rejects_wrong_password():
    password_hash = hash_password("s3cret")

    assert verify_password("wrong", password_hash) is False


@pytest.mark.unit
def test_verify_password_rejects_invalid_hash_without_raising():
    assert verify_password("anything", "not-a-real-hash") is False


@pytest.mark.unit
def test_verify_password_uses_dummy_hash_when_hash_is_empty():
    """Regression test: an empty password_hash (e.g. a user that exists but never had a
    password set) must be rejected via the same slow dummy-hash path as an unknown user's
    missing hash, not a fast short-circuit that would leak timing information."""
    assert verify_password("anything", "") is False


@pytest.mark.unit
def test_get_dummy_hash_is_stable_and_never_verifies():
    """Regression test: the dummy hash used to defend /login against account enumeration by
    timing must stay constant across calls, and must never successfully verify any password -
    otherwise it wouldn't be usable as a safe stand-in for a missing real hash."""
    dummy = get_dummy_hash()

    assert dummy == get_dummy_hash()
    assert verify_password("anything", dummy) is False


# ================================================================================
# REGRESSION TESTS - CSV Import Validation
# ================================================================================


@pytest.mark.regression
def test_csv_import_validates_delimiter(mock_kee):
    """
    Regression test: delimiter must be valid

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
# REGRESSION TESTS - Optimized Search
# ================================================================================


@pytest.mark.regression
def test_csv_import_uses_generator_for_search(tmp_path, mock_kee):
    """
    Regression test: use generator with next()

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
    current_user.groups = ["admin", "users"]

    assert current_user.username == "admin"
    assert current_user.title == "Admin"
    assert current_user.admin is True
    assert current_user.groups == ["admin", "users"]


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
def test_get_cust_attribs_list_excludes_predefined_custom_keys(mock_kee):
    """get_cust_attribs_list should exclude every _UserField custom-property key (superuser,
    msg_login, auth_type, password_changed_at) - native fields (username/title/admin's real key
    "superuser" aside) never appear in custom_properties in real KeePass data, so they aren't
    part of this fixture."""
    entry = Mock()
    entry.tags = []
    entry.custom_properties = ["superuser", "msg_login", "auth_type", "password_changed_at", "service"]
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
    user_uuid = "12345678-1234-1234-1234-123456789012"
    entry = _make_user_entry(user_uuid, "jdoe", ["users"])
    mock_kee.db.find_entries = Mock(return_value=entry)

    users = Users(mock_kee)
    users.users_add_groups([user_uuid], ["admin"])

    assert set(entry.tags) == {"users", "admin"}
    assert mock_kee.save_db.called


@pytest.mark.unit
def test_users_add_groups_ignores_all_group(mock_kee):
    """users_add_groups should never add the 'all' group explicitly"""
    user_uuid = "12345678-1234-1234-1234-123456789012"
    entry = _make_user_entry(user_uuid, "jdoe", ["users"])
    mock_kee.db.find_entries = Mock(return_value=entry)

    users = Users(mock_kee)
    users.users_add_groups([user_uuid], ["all"])

    assert entry.tags == ["users"]
    assert not mock_kee.save_db.called


@pytest.mark.unit
def test_users_add_groups_skips_uuids_with_no_matching_user(mock_kee):
    """users_add_groups should skip uuids that don't match any existing user"""
    mock_kee.db.find_entries = Mock(return_value=None)

    users = Users(mock_kee)
    users.users_add_groups(["12345678-1234-1234-1234-123456789012"], ["admin"])

    assert not mock_kee.save_db.called


@pytest.mark.unit
def test_users_add_groups_updates_every_user_in_a_multi_user_batch(mock_kee):
    """Complements the real-DB integration test that caught the reload-wipes-uncommitted-changes
    bug (test_admin_users_groups_routes.py) - this Mock-based test can't reproduce reload()'s
    destructive effect (see test_user_save_does_not_reload_during_a_batched_save for that), but
    it does pin that the batch loop itself correctly reaches and updates every distinct user,
    not just the first/last one."""
    uuid_a = "12345678-1234-1234-1234-123456789012"
    uuid_b = "87654321-4321-4321-4321-210987654321"
    entry_a = _make_user_entry(uuid_a, "jdoe", ["users"])
    entry_b = _make_user_entry(uuid_b, "asmith", ["users"])
    entries = {uuid_a: entry_a, uuid_b: entry_b}
    mock_kee.db.find_entries = Mock(side_effect=lambda uuid, first: entries[str(uuid)])

    users = Users(mock_kee)
    users.users_add_groups([uuid_a, uuid_b], ["admin"])

    assert set(entry_a.tags) == {"users", "admin"}
    assert set(entry_b.tags) == {"users", "admin"}
    assert mock_kee.save_db.called


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
    user_uuid = "12345678-1234-1234-1234-123456789012"
    entry = _make_user_entry(user_uuid, "jdoe", ["users", "admin"])
    mock_kee.db.find_entries = Mock(return_value=entry)

    users = Users(mock_kee)
    users.users_remove_groups([user_uuid], ["admin"])

    assert entry.tags == ["users"]
    assert mock_kee.save_db.called


@pytest.mark.unit
def test_users_remove_groups_skips_entries_without_tags(mock_kee):
    """users_remove_groups should skip entries with no tags"""
    user_uuid = "12345678-1234-1234-1234-123456789012"
    entry = _make_user_entry(user_uuid, "jdoe", [])
    mock_kee.db.find_entries = Mock(return_value=entry)

    users = Users(mock_kee)
    users.users_remove_groups([user_uuid], ["admin"])

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
    assert "admin" in user.groups


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
def test_auth_type_from_value_is_case_insensitive():
    assert AuthType.from_value("basic") is AuthType.BASIC
    assert AuthType.from_value("BASIC") is AuthType.BASIC
    assert AuthType.from_value("Basic") is AuthType.BASIC
    assert AuthType.from_value("none") is AuthType.NONE


@pytest.mark.unit
def test_auth_type_from_value_returns_none_for_unknown_value():
    assert AuthType.from_value("not_a_real_type") is None


@pytest.mark.unit
def test_user_default_auth_type_is_none(mock_kee):
    users = Users(mock_kee)
    user = User(users, detect_user=False)

    assert user.auth_type == AuthType.NONE
    assert user.password_hash == ""
    assert user.password_changed_at is None


@pytest.mark.unit
def test_password_hash_setter_updates_hash_and_timestamp(mock_kee):
    users = Users(mock_kee)
    user = User(users, detect_user=False)

    user.password_hash = "some-hash"

    assert user.password_hash == "some-hash"
    assert user.password_changed_at is not None


@pytest.mark.unit
def test_password_changed_at_cannot_be_set_directly(mock_kee):
    """password_changed_at is read-only - it can only change as a side effect of assigning
    password_hash, so a caller can't update one without the other."""
    users = Users(mock_kee)
    user = User(users, detect_user=False)

    with pytest.raises(AttributeError):
        setattr(user, "password_changed_at", datetime.now(timezone.utc))


@pytest.mark.unit
def test_user_load_from_entry_sets_password_hash_and_auth_type(mock_kee):
    from pykeepass.entry import Entry

    entry = Mock(spec=Entry)
    entry.uuid = "12345678-1234-1234-1234-123456789012"
    entry.username = "jdoe"
    entry.title = "John Doe"
    entry.password = "argon2-hash-value"
    entry.tags = []
    entry.custom_properties = ["auth_type", "password_changed_at"]

    def get_prop(name):
        return {"auth_type": "basic", "password_changed_at": "2026-01-15T10:30:00"}[name]

    entry.get_custom_property = Mock(side_effect=get_prop)

    users = Users(mock_kee)
    user = User(users, entry=entry)

    assert user.password_hash == "argon2-hash-value"
    assert user.auth_type == AuthType.BASIC
    assert user.password_changed_at == datetime(2026, 1, 15, 10, 30, 0)


@pytest.mark.unit
def test_user_load_from_entry_parses_auth_type_case_insensitively(mock_kee):
    """Regression: _load_from_entry must use AuthType.from_value() (case-insensitive), not the
    raw AuthType(value) constructor - a stray non-canonical value in storage (hand-edited entry,
    or a bug elsewhere) must still resolve correctly rather than silently falling back to NONE."""
    from pykeepass.entry import Entry

    entry = Mock(spec=Entry)
    entry.uuid = "12345678-1234-1234-1234-123456789012"
    entry.username = "jdoe"
    entry.title = "John Doe"
    entry.password = ""
    entry.tags = []
    entry.custom_properties = ["auth_type"]
    entry.get_custom_property = Mock(return_value="BASIC")

    users = Users(mock_kee)
    user = User(users, entry=entry)

    assert user.auth_type == AuthType.BASIC


@pytest.mark.unit
def test_user_load_from_entry_defaults_auth_type_on_unknown_value(mock_kee):
    from pykeepass.entry import Entry

    entry = Mock(spec=Entry)
    entry.uuid = "12345678-1234-1234-1234-123456789012"
    entry.username = "jdoe"
    entry.title = "John Doe"
    entry.password = ""
    entry.tags = []
    entry.custom_properties = ["auth_type"]
    entry.get_custom_property = Mock(return_value="unknown_type")

    users = Users(mock_kee)
    user = User(users, entry=entry)

    assert user.auth_type == AuthType.NONE


@pytest.mark.unit
def test_user_load_from_entry_handles_missing_password_changed_at(mock_kee):
    from pykeepass.entry import Entry

    entry = Mock(spec=Entry)
    entry.uuid = "12345678-1234-1234-1234-123456789012"
    entry.username = "jdoe"
    entry.title = "John Doe"
    entry.password = ""
    entry.tags = []
    entry.custom_properties = []

    users = Users(mock_kee)
    user = User(users, entry=entry)

    assert user.password_changed_at is None


@pytest.mark.unit
def test_user_save_writes_password_hash_auth_type_and_changed_at(mock_kee):
    users = Users(mock_kee)
    user = User(users, detect_user=False)
    user.username = "jdoe"
    user.password_hash = "new-hash"
    user.auth_type = AuthType.BASIC

    new_entry = Mock()
    new_entry.uuid = "new-uuid"
    mock_kee.db.add_entry = Mock(return_value=new_entry)

    user.save()

    assert new_entry.password == "new-hash"
    new_entry.set_custom_property.assert_any_call("auth_type", "basic")
    changed_at_calls = [c for c in new_entry.set_custom_property.call_args_list if c.args[0] == "password_changed_at"]
    assert len(changed_at_calls) == 1
    assert user.password_changed_at is not None
    assert changed_at_calls[0].args[1] == user.password_changed_at.isoformat()


@pytest.mark.unit
def test_user_save_reloads_before_a_standalone_save(mock_kee):
    """Regression: a standalone save (save_db=True) must reload first, so it checks the
    duplicate-username rule against the freshest on-disk state."""
    users = Users(mock_kee)
    user = User(users, detect_user=False)
    user.username = "jdoe"

    new_entry = Mock()
    new_entry.uuid = "new-uuid"
    mock_kee.db.add_entry = Mock(return_value=new_entry)
    mock_kee._open_db.reset_mock()

    user.save(save_db=True)

    # save() also calls get_all_groups(reload=False) at the end, so check the *first* call -
    # the one save() itself makes before doing anything else
    assert mock_kee._open_db.call_args_list[0] == call(True)


@pytest.mark.unit
def test_user_save_does_not_reload_during_a_batched_save(mock_kee):
    """Regression: save(save_db=False) - used by users_add_groups()/users_remove_groups() to
    batch several users' changes before one final save_db() - must NOT reload. Kee._open_db(True)
    calls db.reload(), which discards any sibling user's still-uncommitted change made earlier
    in the same batch - exactly the bug found via a real multi-user integration test (Mocks
    can't reproduce reload()'s destructive effect, which is why this needs pinning explicitly)."""
    users = Users(mock_kee)
    user = User(users, detect_user=False)
    user.username = "jdoe"

    new_entry = Mock()
    new_entry.uuid = "new-uuid"
    mock_kee.db.add_entry = Mock(return_value=new_entry)
    mock_kee._open_db.reset_mock()

    user.save(save_db=False)

    assert mock_kee._open_db.call_args_list[0] == call(False)


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
# csv_import / csv_export - auth/password columns
# ================================================================================


@pytest.mark.unit
def test_csv_import_hashes_plaintext_password_column(tmp_path, mock_kee):
    new_entry = Mock()
    new_entry.username = ""
    new_entry.tags = []
    new_entry.custom_properties = []
    mock_kee.db.add_entry = Mock(return_value=new_entry)

    users = Users(mock_kee)
    csv_file = tmp_path / "users.csv"
    csv_file.write_text(
        "id;description;admin;groups;login message;auth;password\njdoe;John Doe;0;;Welcome;basic;s3cret\n",
        encoding="latin-1",
    )

    users.csv_import(csv_file)

    assert new_entry.password != "s3cret"  # never stored in clear
    assert verify_password("s3cret", new_entry.password) is True


@pytest.mark.unit
def test_csv_import_stores_password_hash_column_unchanged(tmp_path, mock_kee):
    """A password hash column is already a hash - it must be stored as-is, not re-hashed."""
    precomputed_hash = hash_password("s3cret")

    new_entry = Mock()
    new_entry.username = ""
    new_entry.tags = []
    new_entry.custom_properties = []
    mock_kee.db.add_entry = Mock(return_value=new_entry)

    users = Users(mock_kee)
    csv_file = tmp_path / "users.csv"
    csv_file.write_text(
        f"id;description;admin;groups;login message;auth;password hash\n"
        f"jdoe;John Doe;0;;Welcome;basic;{precomputed_hash}\n",
        encoding="latin-1",
    )

    users.csv_import(csv_file)

    assert new_entry.password == precomputed_hash


@pytest.mark.unit
def test_csv_import_writes_auth_type_under_the_real_storage_key(tmp_path, mock_kee):
    """Regression: the CSV column is named "auth", but the KeePass custom property it maps to
    is "auth_type" - the two must not be confused."""
    new_entry = Mock()
    new_entry.username = ""
    new_entry.tags = []
    new_entry.custom_properties = []
    new_entry.set_custom_property = Mock()
    mock_kee.db.add_entry = Mock(return_value=new_entry)

    users = Users(mock_kee)
    csv_file = tmp_path / "users.csv"
    csv_file.write_text(
        "id;description;admin;groups;login message;auth\njdoe;John Doe;0;;Welcome;basic\n",
        encoding="latin-1",
    )

    users.csv_import(csv_file)

    new_entry.set_custom_property.assert_any_call("auth_type", "basic")
    assert ("auth", "basic") not in [c.args for c in new_entry.set_custom_property.call_args_list]


@pytest.mark.unit
def test_csv_import_stores_auth_type_in_canonical_case(tmp_path, mock_kee):
    """Regression: from_value("BASIC") resolves case-insensitively, but the stored value must
    be the canonical "basic" - storing the raw CSV casing would make _load_from_entry's
    case-sensitive AuthType(value) fail on the next load, silently resetting to NONE."""
    new_entry = Mock()
    new_entry.username = ""
    new_entry.tags = []
    new_entry.custom_properties = []
    new_entry.set_custom_property = Mock()
    mock_kee.db.add_entry = Mock(return_value=new_entry)

    users = Users(mock_kee)
    csv_file = tmp_path / "users.csv"
    csv_file.write_text(
        "id;description;admin;groups;login message;auth\njdoe;John Doe;0;;Welcome;BASIC\n",
        encoding="latin-1",
    )

    users.csv_import(csv_file)

    new_entry.set_custom_property.assert_any_call("auth_type", "basic")


@pytest.mark.unit
def test_csv_import_skips_row_with_invalid_auth_type(tmp_path, mock_kee):
    new_entry = Mock()
    new_entry.username = ""
    new_entry.tags = []
    new_entry.custom_properties = []
    new_entry.set_custom_property = Mock()
    mock_kee.db.add_entry = Mock(return_value=new_entry)

    users = Users(mock_kee)
    csv_file = tmp_path / "users.csv"
    csv_file.write_text(
        "id;description;admin;groups;login message;auth\njdoe;John Doe;0;;Welcome;not_a_type\n",
        encoding="latin-1",
    )

    users.csv_import(csv_file)  # must not raise

    assert "auth_type" not in [c.args[0] for c in new_entry.set_custom_property.call_args_list]


@pytest.mark.unit
def test_csv_import_rejects_password_and_password_hash_together(tmp_path, mock_kee):
    users = Users(mock_kee)
    csv_file = tmp_path / "users.csv"
    csv_file.write_text(
        "id;description;admin;groups;login message;password;password hash\njdoe;John Doe;0;;Welcome;s3cret;somehash\n",
        encoding="latin-1",
    )

    with pytest.raises(ValueError, match="can't have both a password and a password_hash"):
        users.csv_import(csv_file)


@pytest.mark.unit
def test_csv_import_keeps_existing_password_when_column_empty(tmp_path, mock_kee):
    existing_entry = Mock()
    existing_entry.username = "jdoe"
    existing_entry.password = "old-hash"
    existing_entry.tags = []
    existing_entry.custom_properties = []
    mock_kee.grp_users.entries = [existing_entry]

    users = Users(mock_kee)
    csv_file = tmp_path / "users.csv"
    csv_file.write_text(
        "id;description;admin;groups;login message;password\njdoe;John Doe;0;;Welcome;\n",
        encoding="latin-1",
    )

    users.csv_import(csv_file)

    assert existing_entry.password == "old-hash"


@pytest.mark.unit
def test_csv_export_includes_auth_and_password_hash_when_requested(mock_kee, tmp_path):
    entry = Mock()
    entry.username = "jdoe"
    entry.title = "John Doe"
    entry.password = "the-hash"
    entry.tags = []
    entry.custom_properties = ["auth_type"]

    def get_prop(name):
        return {"superuser": "false", "msg_login": "Welcome", "auth_type": "basic"}.get(name, "")

    entry.get_custom_property = Mock(side_effect=get_prop)
    mock_kee.grp_users.entries = [entry]

    users = Users(mock_kee)
    output_file = tmp_path / "export.csv"

    users.csv_export(output_file, pwd_infos=True)

    lines = output_file.read_text(encoding="utf-8-sig").splitlines()
    header = lines[0].split(";")
    row = lines[1].split(";")
    assert header == ["Id", "Auth", "Password Hash", "Description", "Admin", "Groups", "Login Message"]
    assert len(row) == len(header)  # regression: header/data column count used to drift apart
    assert dict(zip(header, row))["Password Hash"] == "the-hash"
    assert dict(zip(header, row))["Auth"] == "basic"


@pytest.mark.unit
def test_csv_export_excludes_auth_and_password_hash_by_default(mock_kee, tmp_path):
    entry = Mock()
    entry.username = "jdoe"
    entry.title = "John Doe"
    entry.password = "the-hash"
    entry.tags = []
    entry.custom_properties = []
    entry.get_custom_property = Mock(return_value="")
    mock_kee.grp_users.entries = [entry]

    users = Users(mock_kee)
    output_file = tmp_path / "export.csv"

    users.csv_export(output_file)

    header = output_file.read_text(encoding="utf-8-sig").splitlines()[0]
    assert "Auth" not in header
    assert "Password Hash" not in header
    assert "the-hash" not in output_file.read_text(encoding="utf-8-sig")


# ================================================================================
# _UserField
# ================================================================================


@pytest.mark.unit
def test_user_field_label_title_cases_multi_word_columns():
    assert UserField.LOGIN_MSG.label == "Login Message"
    assert UserField.PWD_HASH.label == "Password Hash"
    assert UserField.ID.label == "Id"


@pytest.mark.unit
def test_user_field_csv_status_splits_standard_from_auth_password_columns():
    """Regression: csv_status is an _CsvStatus member (always truthy), so `if c.csv_status` /
    `if not c.csv_status` are vacuous checks that don't actually discriminate anything - must
    compare against a specific status instead."""
    required = {c for c in UserField if c.csv_status is _CsvStatus.REQUIRED}
    optional = {c for c in UserField if c.csv_status is _CsvStatus.OPTIONAL}

    assert required == {
        UserField.ID,
        UserField.DESCRIPTION,
        UserField.ADMIN,
        UserField.GROUPS,
        UserField.LOGIN_MSG,
    }
    assert optional == {UserField.AUTH_TYPE, UserField.PWD, UserField.PWD_HASH}


@pytest.mark.unit
def test_user_field_pwd_changed_at_is_excluded_but_a_known_custom_key():
    """PWD_CHANGED_AT is never a CSV column (auto-managed via the password_hash setter), but it
    must still be recognized as a predefined KeePass custom property, not a dynamic one."""
    assert UserField.PWD_CHANGED_AT.csv_status is _CsvStatus.EXCLUDED
    assert UserField.PWD_CHANGED_AT not in {c for c in UserField if c.csv_status is _CsvStatus.REQUIRED}
    assert UserField.PWD_CHANGED_AT not in {c for c in UserField if c.csv_status is _CsvStatus.OPTIONAL}
    assert UserField.PWD_CHANGED_AT.db_key in UserField.custom_keys()


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


# ================================================================================
# User.user_info
# ================================================================================


@pytest.mark.unit
def test_user_info_returns_a_predefined_attribute(mock_kee):
    """user_info() should read a predefined User attribute (e.g. admin) directly."""
    users = Users(mock_kee)
    user = User(users, detect_user=False)
    user.admin = True

    assert user.user_info("admin") is True


@pytest.mark.unit
def test_user_info_returns_a_dynamic_custom_attribute(mock_kee):
    """user_info() should fall back to attribs_cust for anything not predefined."""
    users = Users(mock_kee)
    user = User(users, detect_user=False)
    user.attribs_cust = {"service": "IT"}

    assert user.user_info("service") == "IT"


@pytest.mark.unit
def test_user_info_returns_empty_string_for_unknown_attribute(mock_kee):
    """user_info() should return "" (not raise) for an attribute that is neither predefined
    nor a known custom one - e.g. a typo in a query's USER_INFO(...) call."""
    users = Users(mock_kee)
    user = User(users, detect_user=False)

    assert user.user_info("does_not_exist") == ""


@pytest.mark.unit
def test_user_load_from_entry_returns_false_for_falsy_entry(mock_kee):
    """_load_from_entry should return False and do nothing for a falsy entry"""
    from Pytre_Core.users import User

    users = Users(mock_kee)
    user = User(users, detect_user=False)

    assert user._load_from_entry(None) is False  # type: ignore
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
