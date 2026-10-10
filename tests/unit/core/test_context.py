"""
Tests for Pytre/context.py

AppContext is the app-level composition root: it lazily builds and caches
Kee, Settings, Servers, Users and OidcProviders on first access, guarded by a per-attribute
lock for thread safety. In CLIENT mode (no KeePass database) none of them is available.

UserContext is the per-user composition root: it lazily builds and caches
User, UserPrefs, UserLogs and Remotes the same way. Unlike AppContext
it takes a Users instance directly (None without a users database) rather than a whole context,
since that's the only thing it actually depends on to build the user.
"""

import threading
import time

import pytest
from unittest.mock import Mock, patch

from Pytre_Core.context import AppContext, AppMode, UserContext, detect_client_mode
from Pytre_Core.user_prefs import DisplayFormat


# ================================================================================
# AppContext
# ================================================================================


@pytest.fixture
def mock_app_dependencies():
    """Patch every dependency AppContext can construct, to avoid touching a real KeePass db"""
    with (
        patch("Pytre_Core.context.Kee") as MockKee,
        patch("Pytre_Core.context.Settings") as MockSettings,
        patch("Pytre_Core.context.Servers") as MockServers,
        patch("Pytre_Core.context.Users") as MockUsers,
        patch("Pytre_Core.context.OidcProviders") as MockOidcProviders,
    ):
        MockKee.return_value = Mock(name="kee_instance")
        MockSettings.return_value = Mock(name="settings_instance")
        MockServers.return_value = Mock(name="servers_instance")
        MockUsers.return_value = Mock(name="users_instance")
        MockOidcProviders.return_value = Mock(name="oidc_providers_instance")

        yield {
            "Kee": MockKee,
            "Settings": MockSettings,
            "Servers": MockServers,
            "Users": MockUsers,
            "OidcProviders": MockOidcProviders,
        }


@pytest.mark.unit
def test_nothing_is_built_at_construction(mock_app_dependencies):
    """AppContext() should not eagerly build any dependency"""
    AppContext()

    for name, mock_cls in mock_app_dependencies.items():
        assert not mock_cls.called, f"{name} should not be constructed until accessed"


@pytest.mark.unit
def test_kee_is_built_lazily_and_cached(mock_app_dependencies):
    ctx = AppContext()

    kee1 = ctx.kee
    kee2 = ctx.kee

    mock_app_dependencies["Kee"].assert_called_once()
    assert kee1 is kee2


@pytest.mark.unit
def test_kee_receives_master_key(mock_app_dependencies):
    ctx = AppContext(master_key="custom-master-key")

    ctx.kee

    mock_app_dependencies["Kee"].assert_called_once_with(password="custom-master-key")


@pytest.mark.unit
def test_kee_defaults_to_empty_master_key(mock_app_dependencies):
    ctx = AppContext()

    ctx.kee

    mock_app_dependencies["Kee"].assert_called_once_with(password="")


@pytest.mark.unit
def test_settings_receives_kee(mock_app_dependencies):
    ctx = AppContext()

    settings = ctx.settings

    mock_app_dependencies["Settings"].assert_called_once_with(ctx.kee)
    assert settings is mock_app_dependencies["Settings"].return_value


@pytest.mark.unit
def test_servers_receives_kee(mock_app_dependencies):
    ctx = AppContext()

    servers = ctx.servers

    mock_app_dependencies["Servers"].assert_called_once_with(ctx.kee)
    assert servers is mock_app_dependencies["Servers"].return_value


@pytest.mark.unit
def test_users_receives_kee(mock_app_dependencies):
    ctx = AppContext()

    users = ctx.users

    mock_app_dependencies["Users"].assert_called_once_with(ctx.kee)
    assert users is mock_app_dependencies["Users"].return_value


@pytest.mark.unit
def test_oidc_providers_receives_kee(mock_app_dependencies):
    """Regression: the property once asked _get_or_create() for "oidc" while the attribute was
    declared as "oidcs" - the dynamic attributes created by LazyContext are invisible to static
    checks, so only actually reading the property could catch the mismatch."""
    ctx = AppContext()

    oidc_providers = ctx.oidc_providers

    mock_app_dependencies["OidcProviders"].assert_called_once_with(ctx.kee)
    assert oidc_providers is mock_app_dependencies["OidcProviders"].return_value


@pytest.mark.unit
def test_oidc_providers_is_built_lazily_and_cached(mock_app_dependencies):
    ctx = AppContext()

    oidc_providers1 = ctx.oidc_providers
    oidc_providers2 = ctx.oidc_providers

    mock_app_dependencies["OidcProviders"].assert_called_once()
    assert oidc_providers1 is oidc_providers2


@pytest.mark.unit
def test_independent_instances_do_not_share_dependencies(mock_app_dependencies):
    """Two AppContext instances should each build their own set of dependencies"""
    ctx1 = AppContext()
    ctx2 = AppContext()

    ctx1.kee
    ctx2.kee

    assert mock_app_dependencies["Kee"].call_count == 2


@pytest.mark.unit
def test_property_access_is_thread_safe(mock_app_dependencies):
    """Concurrent access to the same property should only construct the dependency once"""
    call_count = 0
    build_started = threading.Event()
    release_build = threading.Event()

    def slow_factory(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        build_started.set()
        release_build.wait(timeout=2)
        return Mock()

    mock_app_dependencies["Kee"].side_effect = slow_factory

    ctx = AppContext()
    results = []

    def access_kee():
        results.append(ctx.kee)

    t1 = threading.Thread(target=access_kee)
    t2 = threading.Thread(target=access_kee)

    t1.start()
    build_started.wait(timeout=2)  # ensure t1 has entered the factory before t2 starts racing it
    t2.start()
    time.sleep(0.05)
    release_build.set()

    t1.join(timeout=2)
    t2.join(timeout=2)

    assert call_count == 1
    assert results[0] is results[1]


# ================================================================================
# AppMode / detect_client_mode
# ================================================================================


@pytest.mark.unit
def test_detect_client_mode_is_hybrid_when_database_exists(tmp_path):
    kee_file = tmp_path / "Pytre.db"
    kee_file.write_bytes(b"")

    assert detect_client_mode(kee_file) is AppMode.HYBRID


@pytest.mark.unit
def test_detect_client_mode_is_client_without_database_and_never_creates_it(tmp_path):
    kee_file = tmp_path / "Pytre.db"

    assert detect_client_mode(kee_file) is AppMode.CLIENT
    assert not kee_file.exists()


@pytest.mark.unit
def test_app_context_defaults_to_server_mode(mock_app_dependencies):
    ctx = AppContext()

    assert ctx.mode is AppMode.SERVER
    assert ctx.acts_as_server is True


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mode", "acts_as_server"), [(AppMode.SERVER, True), (AppMode.HYBRID, True), (AppMode.CLIENT, False)]
)
def test_app_context_acts_as_server(mock_app_dependencies, mode, acts_as_server):
    assert AppContext(mode=mode).acts_as_server is acts_as_server


@pytest.mark.unit
@pytest.mark.parametrize("name", ["kee", "settings", "servers", "users", "oidc_providers"])
def test_app_context_in_client_mode_raises_instead_of_building_keepass_dependencies(mock_app_dependencies, name):
    """In CLIENT mode there is no KeePass database - nothing must be built, especially not Kee which would create it"""
    ctx = AppContext(mode=AppMode.CLIENT)

    with pytest.raises(RuntimeError, match=f"'{name}' is not available in client mode"):
        getattr(ctx, name)

    for dependency, mock_cls in mock_app_dependencies.items():
        assert not mock_cls.called, f"{dependency} should not be constructed in client mode"


@pytest.mark.unit
@pytest.mark.parametrize("name", ["kee", "settings", "servers", "users", "oidc_providers"])
def test_app_context_in_hybrid_mode_builds_keepass_dependencies(mock_app_dependencies, name):
    ctx = AppContext(mode=AppMode.HYBRID)

    assert getattr(ctx, name) is not None


# ================================================================================
# UserContext
# ================================================================================


@pytest.fixture
def mock_user_dependencies():
    """Patch every dependency UserContext can construct, to avoid touching real files/db"""
    with (
        patch("Pytre_Core.context.User") as MockUser,
        patch("Pytre_Core.context.UserPrefs") as MockUserPrefs,
        patch("Pytre_Core.context.UserLogs") as MockUserLogs,
        patch("Pytre_Core.context.Remotes") as MockRemotes,
    ):
        MockUser.return_value = Mock(name="user_instance")
        MockUserPrefs.return_value = Mock(name="user_prefs_instance")
        MockUserLogs.return_value = Mock(name="user_logs_instance")
        MockRemotes.return_value = Mock(name="remotes_instance")

        yield {
            "User": MockUser,
            "UserPrefs": MockUserPrefs,
            "UserLogs": MockUserLogs,
            "Remotes": MockRemotes,
        }


@pytest.fixture
def mock_users():
    return Mock(name="users_instance")


@pytest.mark.unit
def test_user_context_nothing_is_built_at_construction(mock_user_dependencies, mock_users):
    """UserContext() should not eagerly build any dependency"""
    UserContext(mock_users)

    for name, mock_cls in mock_user_dependencies.items():
        assert not mock_cls.called, f"{name} should not be constructed until accessed"


@pytest.mark.unit
def test_user_receives_users(mock_user_dependencies, mock_users):
    ctx = UserContext(mock_users)

    user = ctx.user

    mock_user_dependencies["User"].assert_called_once_with(mock_users, username="")
    assert user is mock_user_dependencies["User"].return_value


@pytest.mark.unit
def test_user_forwards_explicit_username(mock_user_dependencies, mock_users):
    ctx = UserContext(mock_users, username="jdoe")

    ctx.user

    mock_user_dependencies["User"].assert_called_once_with(mock_users, username="jdoe")


@pytest.mark.unit
def test_user_is_built_lazily_and_cached(mock_user_dependencies, mock_users):
    ctx = UserContext(mock_users)

    user1 = ctx.user
    user2 = ctx.user

    mock_user_dependencies["User"].assert_called_once()
    assert user1 is user2


@pytest.mark.unit
def test_user_prefs_is_built_lazily_and_cached(mock_user_dependencies, mock_users):
    ctx = UserContext(mock_users)

    prefs1 = ctx.user_prefs
    prefs2 = ctx.user_prefs

    mock_user_dependencies["UserPrefs"].assert_called_once_with()
    assert prefs1 is prefs2


@pytest.mark.unit
def test_user_logs_is_built_lazily_and_cached(mock_user_dependencies, mock_users):
    ctx = UserContext(mock_users)

    db1 = ctx.user_logs
    db2 = ctx.user_logs

    mock_user_dependencies["UserLogs"].assert_called_once_with()
    assert db1 is db2


@pytest.mark.unit
def test_remotes_is_built_without_arguments(mock_user_dependencies, mock_users):
    """Remotes no longer depends on UserPrefs - it manages its own dedicated storage file."""
    ctx = UserContext(mock_users)

    remotes = ctx.remotes

    mock_user_dependencies["Remotes"].assert_called_once_with()
    assert remotes is mock_user_dependencies["Remotes"].return_value


@pytest.mark.unit
def test_remotes_is_built_lazily_and_cached(mock_user_dependencies, mock_users):
    ctx = UserContext(mock_users)

    remotes1 = ctx.remotes
    remotes2 = ctx.remotes

    mock_user_dependencies["Remotes"].assert_called_once()
    assert remotes1 is remotes2


@pytest.mark.unit
def test_user_context_independent_instances_do_not_share_dependencies(mock_user_dependencies, mock_users):
    """Two UserContext instances should each build their own set of dependencies"""
    ctx1 = UserContext(mock_users)
    ctx2 = UserContext(mock_users)

    ctx1.user
    ctx2.user

    assert mock_user_dependencies["User"].call_count == 2


@pytest.mark.unit
def test_user_context_property_access_is_thread_safe(mock_user_dependencies, mock_users):
    """Concurrent access to the same property should only construct the dependency once"""
    call_count = 0
    build_started = threading.Event()
    release_build = threading.Event()

    def slow_factory(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        build_started.set()
        release_build.wait(timeout=2)
        return Mock()

    mock_user_dependencies["User"].side_effect = slow_factory

    ctx = UserContext(mock_users)
    results = []

    def access_user():
        results.append(ctx.user)

    t1 = threading.Thread(target=access_user)
    t2 = threading.Thread(target=access_user)

    t1.start()
    build_started.wait(timeout=2)
    t2.start()
    time.sleep(0.05)
    release_build.set()

    t1.join(timeout=2)
    t2.join(timeout=2)

    assert call_count == 1
    assert results[0] is results[1]


@pytest.mark.unit
def test_with_remote_user_infos_returns_self_without_infos(mock_user_dependencies, mock_users):
    ctx = UserContext(mock_users)

    assert ctx.with_remote_user_infos(None) is ctx
    assert ctx.with_remote_user_infos({}) is ctx
    mock_user_dependencies["User"].create_from_remote_infos.assert_not_called()


@pytest.mark.unit
def test_with_remote_user_infos_builds_context_overriding_user(mock_user_dependencies, mock_users):
    infos = {"username": "alice"}
    ctx = UserContext(mock_users)
    mock_user_cls = mock_user_dependencies["User"]

    derived = ctx.with_remote_user_infos(infos)

    mock_user_cls.create_from_remote_infos.assert_called_once_with(mock_users, infos)
    assert derived is not ctx
    assert derived.user is mock_user_cls.create_from_remote_infos.return_value
    mock_user_cls.assert_not_called()  # no lookup in the local users db


@pytest.mark.unit
def test_with_remote_user_infos_shares_prefs_logs_and_remotes(mock_user_dependencies, mock_users):
    ctx = UserContext(mock_users)

    derived = ctx.with_remote_user_infos({"username": "alice"})

    assert derived.user_prefs is ctx.user_prefs
    assert derived.user_logs is ctx.user_logs
    assert derived.remotes is ctx.remotes


@pytest.mark.unit
def test_with_remote_user_infos_stays_lazy(mock_user_dependencies, mock_users):
    ctx = UserContext(mock_users)

    ctx.with_remote_user_infos({"username": "alice"})

    for name in ("UserPrefs", "UserLogs", "Remotes"):
        assert not mock_user_dependencies[name].called, f"{name} should not be built by with_remote_user_infos()"


@pytest.mark.unit
def test_with_remote_user_infos_flattens_chained_derivation(mock_user_dependencies, mock_users):
    """Deriving from an already-derived context must not stack indirection - _base always points
    at the root context, never at another derived one."""
    root = UserContext(mock_users)
    intermediate = root.with_remote_user_infos({"username": "alice"})

    derived = intermediate.with_remote_user_infos({"username": "bob"})

    assert derived._base is root


@pytest.mark.unit
def test_user_without_users_database_is_built_with_none(mock_user_dependencies):
    """CLIENT mode: no users database, User gets None and resolves a local user on its own"""
    ctx = UserContext(None)

    ctx.user

    mock_user_dependencies["User"].assert_called_once_with(None, username="")


@pytest.mark.unit
def test_user_override_is_returned_as_is(mock_user_dependencies, mock_users):
    override = Mock(name="override_user")
    ctx = UserContext(mock_users, user_override=override)

    assert ctx.user is override
    mock_user_dependencies["User"].assert_not_called()


@pytest.mark.unit
def test_display_format_comes_from_user_prefs(mock_user_dependencies, mock_users):
    ctx = UserContext(mock_users)

    assert ctx.display_format is mock_user_dependencies["UserPrefs"].return_value.display_format


@pytest.mark.unit
def test_display_format_override_is_returned_as_is(mock_user_dependencies, mock_users):
    display_format = DisplayFormat(date_format="%Y-%m-%d")
    ctx = UserContext(mock_users, display_format_override=display_format)

    assert ctx.display_format is display_format
    mock_user_dependencies["UserPrefs"].assert_not_called()


@pytest.mark.unit
def test_with_remote_user_infos_keeps_display_format_override(mock_user_dependencies, mock_users):
    display_format = DisplayFormat(date_format="%Y-%m-%d")
    ctx = UserContext(mock_users, display_format_override=display_format)

    derived = ctx.with_remote_user_infos({"username": "alice"})

    assert derived.display_format is display_format
