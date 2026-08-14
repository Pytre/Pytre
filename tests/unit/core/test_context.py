"""
Tests for Pytre/context.py

AppContext is the app-level composition root: it lazily builds and caches
Kee, Settings, Servers and Users on first access, guarded by a per-attribute
lock for thread safety.

UserContext is the per-user composition root: it lazily builds and caches
User (current user), UserPrefs, UserLogs and Remotes the same way. Unlike AppContext
it takes a Users instance directly rather than a whole context, since that's
the only thing it actually depends on to build the current user.
"""

import threading
import time

import pytest
from unittest.mock import Mock, patch

from Pytre_Core.context import AppContext, UserContext


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
    ):
        MockKee.return_value = Mock(name="kee_instance")
        MockSettings.return_value = Mock(name="settings_instance")
        MockServers.return_value = Mock(name="servers_instance")
        MockUsers.return_value = Mock(name="users_instance")

        yield {
            "Kee": MockKee,
            "Settings": MockSettings,
            "Servers": MockServers,
            "Users": MockUsers,
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
        MockUser.return_value = Mock(name="current_user_instance")
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
def test_current_user_receives_users(mock_user_dependencies, mock_users):
    ctx = UserContext(mock_users)

    current_user = ctx.current_user

    mock_user_dependencies["User"].assert_called_once_with(mock_users, username="")
    assert current_user is mock_user_dependencies["User"].return_value


@pytest.mark.unit
def test_current_user_forwards_explicit_username(mock_user_dependencies, mock_users):
    ctx = UserContext(mock_users, username="jdoe")

    ctx.current_user

    mock_user_dependencies["User"].assert_called_once_with(mock_users, username="jdoe")


@pytest.mark.unit
def test_current_user_is_built_lazily_and_cached(mock_user_dependencies, mock_users):
    ctx = UserContext(mock_users)

    user1 = ctx.current_user
    user2 = ctx.current_user

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

    ctx1.current_user
    ctx2.current_user

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

    def access_current_user():
        results.append(ctx.current_user)

    t1 = threading.Thread(target=access_current_user)
    t2 = threading.Thread(target=access_current_user)

    t1.start()
    build_started.wait(timeout=2)
    t2.start()
    time.sleep(0.05)
    release_build.set()

    t1.join(timeout=2)
    t2.join(timeout=2)

    assert call_count == 1
    assert results[0] is results[1]
