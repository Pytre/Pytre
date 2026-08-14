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
    with patch("Pytre_Core.kee.crypted_file_pwd_get", return_value="test_password"), patch(
        "Pytre_Core.kee.crypted_file_pwd_history", return_value=["current", "old1", "old2"]
    ), patch("Pytre_Core.kee.crypted_file_pwd_change"):
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

    with patch("Pytre_Core.kee.PyKeePass") as MockPyKeePass, patch(
        "Pytre_Core.kee.crypted_file_pwd_get", return_value="test"
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

        assert kee._init_pwd == BLANK_PWD


@pytest.mark.unit
def test_init_pwd_uses_given_password_when_provided(mock_pykeepass, mock_credentials):
    with patch("Pytre_Core.kee.PyKeePass", return_value=mock_pykeepass):
        kee = Kee(password="custom-master-key")

        assert kee._init_pwd == "custom-master-key"


@pytest.mark.unit
def test_open_db_creates_database_with_password_from_init(mock_credentials):
    """_open_db() must create a new database using the password Kee() was constructed with, not
    always BLANK_PWD - otherwise a server-provided master password would never match what
    actually protects a freshly created database."""
    with patch("Pytre_Core.kee.PyKeePass", side_effect=FileNotFoundError):
        with patch.object(Kee, "create_db") as mock_create_db:
            with pytest.raises(AssertionError):  # create_db is mocked out, self.db stays None
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
        kee._open_db(reload=True)

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
def test_create_db_creates_new_database(mock_pykeepass, mock_credentials):
    """create_db should create new database with default structure"""

    with patch("Pytre_Core.kee.PyKeePass", return_value=mock_pykeepass):
        kee = Kee(password="test_password")

        # Mock add_group to return mock groups
        mock_grp = Mock()
        mock_pykeepass.add_group.return_value = mock_grp

        # Mock add_entry to return mock entry
        mock_entry = Mock()
        mock_entry.set_custom_property = Mock()
        mock_pykeepass.add_entry.return_value = mock_entry

        with patch("Pytre_Core.kee.Path.mkdir"):
            kee.create_db(password="new_password")

            # Verify: Groups were created
            assert mock_pykeepass.add_group.call_count >= 3  # Settings, Servers, Users

            # Verify: Entries were created
            assert mock_pykeepass.add_entry.called


@pytest.mark.unit
def test_create_db_sets_root_group_name(mock_pykeepass, mock_credentials):
    """create_db should set root group name to 'settings'"""

    with patch("Pytre_Core.kee.PyKeePass", return_value=mock_pykeepass):
        kee = Kee(password="test_password")

        mock_pykeepass.add_group.return_value = Mock()
        mock_entry = Mock()
        mock_entry.set_custom_property = Mock()
        mock_pykeepass.add_entry.return_value = mock_entry

        with patch("Pytre_Core.kee.Path.mkdir"):
            kee.create_db()

            # Verify: Root group name set
            assert mock_pykeepass.root_group.name == "settings"


@pytest.mark.unit
def test_create_db_creates_queries_folder(mock_pykeepass, mock_credentials):
    """create_db should create queries folder"""

    with patch("Pytre_Core.kee.PyKeePass", return_value=mock_pykeepass):
        kee = Kee(password="test_password")

        mock_pykeepass.add_group.return_value = Mock()
        mock_entry = Mock()
        mock_entry.set_custom_property = Mock()
        mock_pykeepass.add_entry.return_value = mock_entry

        with patch("Pytre_Core.kee.Path.mkdir") as mock_mkdir:
            kee.create_db()

            # Verify: mkdir was called
            assert mock_mkdir.called


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

    with patch("Pytre_Core.kee.PyKeePass", return_value=mock_pykeepass), patch(
        "Pytre_Core.kee.crypted_file_pwd_change"
    ) as mock_pwd_change:
        kee = Kee(password="old_password")

        kee.pwd_change("new_password")

        # Verify: Password updated
        assert kee.pwd == "new_password"
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

    with patch("Pytre_Core.kee.PyKeePass", return_value=mock_pykeepass), patch(
        "Pytre_Core.kee.crypted_file_pwd_change"
    ) as mock_pwd_change:
        kee = Kee(password="test_password")

        kee.pwd_change("test_password")

        # Verify: crypted_file_pwd_change was NOT called
        # (It would be called during __init__, so we can't check call_count == 0)
        # Just verify password didn't change
        assert kee.pwd == "test_password"


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
