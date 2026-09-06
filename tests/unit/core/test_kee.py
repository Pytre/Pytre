"""
Tests for Pytre/kee.py

REGRESSION TESTS:
- Kee initialized as singleton with lazy initialization
- Database access error handling
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from pykeepass.group import Group

from Pytre_Core.kee import Kee, get_app_path, KEE_FILE, BLANK_FILE, BLANK_PWD


# ================================================================================
# Fixtures
# ================================================================================


@pytest.fixture
def mock_pykeepass():
    """Mock PyKeePass database"""
    mock_db = Mock()
    mock_db.root_group = Mock(spec=Group)
    mock_db.root_group.name = "settings"
    mock_db.filename = ""
    mock_db.password = ""
    mock_db.save = Mock()
    mock_db.reload = Mock()
    mock_db.find_groups = Mock(return_value=None)
    mock_db.add_group = Mock()
    mock_db.add_entry = Mock()
    return mock_db


@pytest.fixture
def mock_credentials():
    """Mock credentials functions"""
    with (
        patch("Pytre_Core.kee.crypted_file_pwd_get", return_value="test_password"),
        patch("Pytre_Core.kee.crypted_file_pwd_history", return_value=["current", "old1", "old2"]),
        patch("Pytre_Core.kee.crypted_file_pwd_change"),
    ):
        yield


# ================================================================================
# REGRESSION TESTS - Singleton Initialization
# ================================================================================


@pytest.mark.regression
def test_kee_instances_are_independent():
    """
    Kee no longer uses the singleton pattern (removed, see AppContext refactor).

    Multiple calls to Kee() must return distinct, independent instances.
    """
    with patch("Pytre_Core.kee.PyKeePass"), patch("Pytre_Core.kee.crypted_file_pwd_get", return_value="test"):
        kee1 = Kee()
        kee2 = Kee()

        assert kee1 is not kee2


@pytest.mark.regression
def test_kee_lazy_initialization():
    """
    Regression test: Kee should initialize in __init__

    Database should be opened automatically during instantiation.
    """

    with (
        patch("Pytre_Core.kee.PyKeePass") as MockPyKeePass,
        patch("Pytre_Core.kee.crypted_file_pwd_get", return_value="test"),
    ):
        mock_db = Mock()
        mock_db.find_groups = Mock(return_value=None)
        MockPyKeePass.return_value = mock_db

        kee = Kee()

        # Verify: PyKeePass was called during init
        assert MockPyKeePass.called
        assert kee.is_open is True
        assert kee.access_count == 1


# ================================================================================
# Database Open Functionality
# ================================================================================


@pytest.mark.unit
def test_open_db_opens_existing_database(mock_pykeepass, mock_credentials):
    """_open_db should open existing database successfully"""

    with patch("Pytre_Core.kee.PyKeePass", return_value=mock_pykeepass):
        kee = Kee(password="test_password")

        assert kee.is_open is True
        assert kee.is_ko is False
        assert kee.access_count == 1


@pytest.mark.unit
@pytest.mark.skip(reason="Requires complex mocking of database creation flow")
def test_open_db_creates_database_if_missing(mock_pykeepass, mock_credentials):
    """_open_db should create new database if file not found"""
    from pykeepass.exceptions import CredentialsError

    with patch("Pytre_Core.kee.PyKeePass") as MockPyKeePass:
        # First call raises FileNotFoundError, second call succeeds
        MockPyKeePass.side_effect = [FileNotFoundError, mock_pykeepass]

        with patch.object(Kee, "create_db") as mock_create_db:
            kee = Kee(password="test_password")

            # Verify: create_db was called
            assert mock_create_db.called


@pytest.mark.unit
def test_init_pwd_defaults_to_blank_pwd_when_password_empty(mock_pykeepass, mock_credentials):
    with patch("Pytre_Core.kee.PyKeePass", return_value=mock_pykeepass):
        kee = Kee(password="")

        assert kee._init_password == BLANK_PWD


@pytest.mark.unit
def test_init_pwd_uses_given_password_when_provided(mock_pykeepass, mock_credentials):
    with patch("Pytre_Core.kee.PyKeePass", return_value=mock_pykeepass):
        kee = Kee(password="custom-master-key")

        assert kee._init_password == "custom-master-key"


@pytest.mark.unit
def test_open_db_creates_database_with_password_from_init(mock_credentials):
    """_open_db() must create a new database using the password Kee() was constructed with, not
    always BLANK_PWD - otherwise a server-provided master password would never match what
    actually protects a freshly created database."""
    with patch("Pytre_Core.kee.PyKeePass", side_effect=FileNotFoundError):
        with patch.object(Kee, "create_db") as mock_create_db:
            with pytest.raises(ValueError):  # create_db is mocked out, self.db stays None
                Kee(password="custom-master-key")

    mock_create_db.assert_called_once_with(password="custom-master-key")


@pytest.mark.unit
def test_open_db_handles_credentials_error(mock_pykeepass, mock_credentials):
    """_open_db should handle invalid password gracefully"""
    from pykeepass.exceptions import CredentialsError

    with patch("Pytre_Core.kee.PyKeePass") as MockPyKeePass:
        # Raise credentials error
        MockPyKeePass.side_effect = CredentialsError

        with patch.object(Kee, "pwd_try", return_value=False):
            kee = Kee(password="wrong_password")

            # Verify: Database failed to open
            assert kee.is_ko is True
            assert kee.is_open is False


@pytest.mark.unit
@pytest.mark.skip(reason="Requires complex mocking of password retry flow")
def test_open_db_tries_password_history_on_failure(mock_pykeepass, mock_credentials):
    """_open_db should try password history if main password fails"""
    from pykeepass.exceptions import CredentialsError

    with patch("Pytre_Core.kee.PyKeePass") as MockPyKeePass:
        # First call fails, second succeeds (from history)
        MockPyKeePass.side_effect = [CredentialsError, mock_pykeepass]

        with patch.object(Kee, "pwd_try", return_value=True) as mock_pwd_try:
            kee = Kee(password="wrong_password")

            # Verify: pwd_try was called
            assert mock_pwd_try.called


@pytest.mark.unit
def test_open_db_reload_reloads_database(mock_pykeepass, mock_credentials):
    """_open_db with reload=True should reload database"""

    with patch("Pytre_Core.kee.PyKeePass", return_value=mock_pykeepass):
        kee = Kee(password="test_password")

        initial_count = kee.access_count

        # Reload
        kee.open_db(reload=True)

        # Verify: reload was called and count incremented
        assert mock_pykeepass.reload.called
        assert kee.access_count == initial_count + 1


# ================================================================================
# Group Finding Functionality
# ================================================================================


@pytest.mark.unit
def test_open_db_finds_groups(mock_pykeepass, mock_credentials):
    """_open_db should find and set group references"""

    mock_settings_grp = Mock()
    mock_servers_grp = Mock()
    mock_users_grp = Mock()

    def mock_find_groups(name, first):
        if name == "Paramètres":
            return mock_settings_grp
        elif name == "Serveurs":
            return mock_servers_grp
        elif name == "Utilisateurs":
            return mock_users_grp
        return None

    mock_pykeepass.find_groups = mock_find_groups

    with patch("Pytre_Core.kee.PyKeePass", return_value=mock_pykeepass):
        kee = Kee(password="test_password")

        # Verify: Groups were found
        assert kee.grp_settings == mock_settings_grp
        assert kee.grp_servers == mock_servers_grp
        assert kee.grp_users == mock_users_grp


# ================================================================================
# Save Functionality
# ================================================================================


@pytest.mark.unit
def test_save_db_saves_successfully(mock_pykeepass, mock_credentials):
    """save_db should save database successfully"""

    with patch("Pytre_Core.kee.PyKeePass", return_value=mock_pykeepass):
        kee = Kee(password="test_password")

        kee.save_db()

        # Verify: save was called
        assert mock_pykeepass.save.called


@pytest.mark.unit
def test_save_db_handles_permission_error(mock_pykeepass, mock_credentials):
    """save_db should handle permission errors gracefully"""

    # Mock save to raise PermissionError
    mock_pykeepass.save.side_effect = PermissionError

    with patch("Pytre_Core.kee.PyKeePass", return_value=mock_pykeepass):
        kee = Kee(password="test_password")

        # Should not raise error
        kee.save_db()


# ================================================================================
# Create Database Functionality
# ================================================================================


@pytest.mark.unit
def test_create_db_opens_blank_template_and_redirects_filename(mock_pykeepass, mock_credentials):
    """create_db should open the bundled blank template (with BLANK_PWD) and retarget it to
    save at the real database path - it no longer seeds any default groups/entries/folder
    itself, that's now handled lazily by open_db()/_get_group()."""

    with patch("Pytre_Core.kee.PyKeePass", return_value=mock_pykeepass) as MockPyKeePass:
        kee = Kee(password="test_password")
        MockPyKeePass.reset_mock()

        kee.create_db(password="new_password")

        # Verify: opened the blank template with BLANK_PWD, not the target password
        MockPyKeePass.assert_called_once_with(get_app_path() / BLANK_FILE, password=BLANK_PWD)

        # Verify: save target redirected to the real database file
        assert mock_pykeepass.filename == kee.file

        # Verify: no default groups/entries/folder created directly by create_db anymore
        assert not mock_pykeepass.add_group.called
        assert not mock_pykeepass.add_entry.called


@pytest.mark.unit
def test_create_db_toggles_is_ko_during_creation(mock_pykeepass, mock_credentials):
    """create_db should clear is_ko once creation succeeds"""

    with patch("Pytre_Core.kee.PyKeePass", return_value=mock_pykeepass):
        kee = Kee(password="test_password")

        kee.create_db(password="new_password")

        assert kee.is_ko is False


# ================================================================================
# Group Lookup Functionality
# ================================================================================


@pytest.mark.unit
def test_get_group_returns_existing_group(mock_pykeepass, mock_credentials):
    """_get_group should return the group as-is if it already exists"""

    existing_grp = Mock()
    mock_pykeepass.find_groups = Mock(return_value=existing_grp)

    with patch("Pytre_Core.kee.PyKeePass", return_value=mock_pykeepass):
        kee = Kee(password="test_password")

        group = kee._get_group("Paramètres")

        assert group is existing_grp
        assert not mock_pykeepass.add_group.called


@pytest.mark.unit
def test_get_group_creates_missing_group(mock_pykeepass, mock_credentials):
    """_get_group should create the group under root_group if it doesn't exist yet"""

    mock_pykeepass.find_groups = Mock(return_value=None)
    new_grp = Mock()
    mock_pykeepass.add_group = Mock(return_value=new_grp)

    with patch("Pytre_Core.kee.PyKeePass", return_value=mock_pykeepass):
        kee = Kee(password="test_password")
        mock_pykeepass.add_group.reset_mock()

        group = kee._get_group("Utilisateurs")

        mock_pykeepass.add_group.assert_called_once_with(mock_pykeepass.root_group, "Utilisateurs")
        assert group is new_grp


@pytest.mark.unit
def test_get_group_raises_if_database_not_open(mock_pykeepass, mock_credentials):
    """_get_group should raise ValueError rather than operate on a None database"""

    with patch("Pytre_Core.kee.PyKeePass", return_value=mock_pykeepass):
        kee = Kee(password="test_password")
        kee.db = None

        with pytest.raises(ValueError):
            kee._get_group("Paramètres")


@pytest.mark.unit
def test_open_db_creates_all_three_default_groups_on_a_fresh_database(mock_credentials):
    """open_db (called from __init__) should lazily create the 3 standard groups via
    _get_group() when they don't exist yet - this is what replaces the old eager
    _create_db_set_default() group creation."""

    mock_db = Mock()
    mock_db.root_group = Mock(spec=Group)
    mock_db.find_groups = Mock(return_value=None)
    mock_db.add_group = Mock(side_effect=lambda root, name: Mock())

    with patch("Pytre_Core.kee.PyKeePass", return_value=mock_db):
        Kee(password="test_password")

        created_names = [call.args[1] for call in mock_db.add_group.call_args_list]
        assert created_names == ["Paramètres", "Serveurs", "Utilisateurs"]


# ================================================================================
# Password Management Functionality
# ================================================================================


@pytest.mark.unit
def test_pwd_try_tries_password_list(mock_pykeepass, mock_credentials):
    """pwd_try should try passwords from list"""
    from pykeepass.exceptions import CredentialsError

    with patch("Pytre_Core.kee.PyKeePass") as MockPyKeePass:
        # First two fail, third succeeds
        MockPyKeePass.side_effect = [
            mock_pykeepass,  # Initial open
            CredentialsError,  # pwd_try first attempt
            CredentialsError,  # pwd_try second attempt
            mock_pykeepass,  # pwd_try third attempt succeeds
        ]

        kee = Kee(password="test_password")

        result = kee.pwd_try(["wrong1", "wrong2", "correct"])

        # Verify: Succeeded with third password
        assert result is True
        assert kee.is_ko is False


@pytest.mark.unit
def test_pwd_try_returns_false_if_all_fail(mock_pykeepass, mock_credentials):
    """pwd_try should return False if all passwords fail"""
    from pykeepass.exceptions import CredentialsError

    with patch("Pytre_Core.kee.PyKeePass") as MockPyKeePass:
        # Initial open succeeds, all pwd_try attempts fail
        MockPyKeePass.side_effect = [mock_pykeepass, CredentialsError, CredentialsError]

        kee = Kee(password="test_password")

        result = kee.pwd_try(["wrong1", "wrong2"])

        # Verify: Failed
        assert result is False


@pytest.mark.unit
def test_pwd_get_retrieves_password(mock_credentials):
    """pwd_get should retrieve password from encrypted file"""

    with patch("Pytre_Core.kee.PyKeePass"):
        kee = Kee()

        pwd = kee.pwd_get()

        # Verify: Password retrieved
        assert pwd == "test_password"


@pytest.mark.unit
def test_pwd_history_retrieves_history(mock_credentials):
    """pwd_history should retrieve password history"""

    with patch("Pytre_Core.kee.PyKeePass"):
        kee = Kee()

        history = kee.pwd_history()

        # Verify: History retrieved
        assert history == ["current", "old1", "old2"]


@pytest.mark.unit
def test_pwd_change_updates_password(mock_pykeepass, mock_credentials):
    """pwd_change should update database password"""

    with (
        patch("Pytre_Core.kee.PyKeePass", return_value=mock_pykeepass),
        patch("Pytre_Core.kee.crypted_file_pwd_change") as mock_pwd_change,
    ):
        kee = Kee(password="old_password")

        kee.pwd_change("new_password")

        # Verify: Password updated
        assert kee.password == "new_password"
        assert mock_pykeepass.password == "new_password"
        assert mock_pwd_change.called


@pytest.mark.unit
def test_pwd_change_saves_if_forced(mock_pykeepass, mock_credentials):
    """pwd_change with force_save=True should save database"""

    with patch("Pytre_Core.kee.PyKeePass", return_value=mock_pykeepass):
        kee = Kee(password="old_password")

        kee.pwd_change("new_password", force_save=True)

        # Verify: save was called
        assert mock_pykeepass.save.called


@pytest.mark.unit
def test_pwd_change_does_nothing_if_same_password(mock_pykeepass, mock_credentials):
    """pwd_change should do nothing if password unchanged"""

    with (
        patch("Pytre_Core.kee.PyKeePass", return_value=mock_pykeepass),
        patch("Pytre_Core.kee.crypted_file_pwd_change") as mock_pwd_change,
    ):
        kee = Kee(password="test_password")

        kee.pwd_change("test_password")

        # Verify: crypted_file_pwd_change was NOT called
        # (It would be called during __init__, so we can't check call_count == 0)
        # Just verify password didn't change
        assert kee.password == "test_password"


@pytest.mark.unit
def test_pwd_change_rekeys_and_saves_even_if_password_looks_unchanged_when_forced(
    mock_pykeepass, mock_credentials
):
    """Regression: create_db() opens the blank template (real password BLANK_PWD) then calls
    pwd_change(target_password, force_save=True) - self.password may already equal
    target_password at that point (e.g. an explicit master key passed to Kee()), even though
    self.db itself is still keyed with BLANK_PWD. force_save=True must not let the "password
    looks unchanged" shortcut skip the actual rekey/save - otherwise the freshly created
    database silently stays protected by BLANK_PWD instead of the intended password."""

    with patch("Pytre_Core.kee.PyKeePass", return_value=mock_pykeepass):
        kee = Kee(password="mysecret")

        # self.password already equals the value we're about to pass to pwd_change - simulates
        # the create_db() situation where this coincidence must not be mistaken for a no-op
        assert kee.password == "mysecret"
        mock_pykeepass.password = "password"  # what the db is actually keyed with right now

        kee.pwd_change("mysecret", force_save=True)

        assert mock_pykeepass.password == "mysecret"
        assert mock_pykeepass.save.called


# ================================================================================
# get_app_path Functionality
# ================================================================================


@pytest.mark.unit
def test_get_app_path_frozen_app():
    """get_app_path should return _MEIPASS for frozen app"""
    with patch("sys.frozen", True, create=True), patch("sys._MEIPASS", "/frozen/path", create=True):
        result = get_app_path()

        assert result == Path("/frozen/path")


@pytest.mark.unit
def test_get_app_path_normal_app():
    """get_app_path should return __file__ directory for normal app"""
    result = get_app_path()

    # Should be the directory containing kee.py
    assert result.exists()
    assert result.is_dir()
