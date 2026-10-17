"""
Tests for Pytre_Server/master_key.py
"""

from unittest.mock import Mock

import keyring
import keyring.errors
import pytest

from Pytre_Server.master_key import (
    clear_terminal,
    delete_master_key,
    delete_master_key_interactive,
    get_master_key,
    get_master_key_interactive,
    set_master_key,
    set_master_key_interactive,
    show_master_key_interactive,
)

SERVICE = "Pytre_Server"
USERNAME = "master_key"


@pytest.fixture(autouse=True)
def mock_subprocess_run(monkeypatch):
    """Prevents clear_terminal() from spawning a real subprocess during tests."""
    run = Mock()
    monkeypatch.setattr("Pytre_Server.master_key.subprocess.run", run)
    return run


# ================================================================================
# get_master_key / set_master_key
# ================================================================================


@pytest.mark.unit
def test_get_master_key_returns_none_when_absent(fake_keyring):
    assert get_master_key() is None


@pytest.mark.unit
def test_get_master_key_returns_stored_value(fake_keyring):
    fake_keyring[(SERVICE, USERNAME)] = "s3cret"

    assert get_master_key() == "s3cret"


@pytest.mark.unit
def test_set_master_key_stores_a_real_password(fake_keyring):
    """Regression test: set_master_key() must accept a plain KeePass password, not just
    Fernet-shaped values - it must never try to build a Fernet cipher from it."""
    set_master_key("a real KeePass master password, not a Fernet key")

    assert fake_keyring[(SERVICE, USERNAME)] == "a real KeePass master password, not a Fernet key"


@pytest.mark.unit
def test_set_master_key_overwrites_existing_value(fake_keyring):
    fake_keyring[(SERVICE, USERNAME)] = "old-pwd"

    set_master_key("new-pwd")

    assert fake_keyring[(SERVICE, USERNAME)] == "new-pwd"


# ================================================================================
# delete_master_key
# ================================================================================


@pytest.mark.unit
def test_delete_master_key_removes_existing_entry(fake_keyring):
    fake_keyring[(SERVICE, USERNAME)] = "s3cret"

    delete_master_key()

    assert (SERVICE, USERNAME) not in fake_keyring


@pytest.mark.unit
def test_delete_master_key_is_a_noop_when_nothing_to_delete(fake_keyring):
    delete_master_key()  # must not raise


# ================================================================================
# delete_master_key_interactive
# ================================================================================


@pytest.mark.unit
def test_delete_interactive_deletes_when_confirmed(fake_keyring, monkeypatch):
    fake_keyring[(SERVICE, USERNAME)] = "s3cret"
    monkeypatch.setattr("builtins.input", Mock(return_value="y"))

    delete_master_key_interactive()

    assert (SERVICE, USERNAME) not in fake_keyring


@pytest.mark.unit
def test_delete_interactive_keeps_value_when_not_confirmed(fake_keyring, monkeypatch):
    fake_keyring[(SERVICE, USERNAME)] = "s3cret"
    monkeypatch.setattr("builtins.input", Mock(return_value="n"))

    delete_master_key_interactive()

    assert fake_keyring[(SERVICE, USERNAME)] == "s3cret"


@pytest.mark.unit
def test_delete_interactive_does_nothing_when_absent(fake_keyring, monkeypatch):
    prompt = Mock()
    monkeypatch.setattr("builtins.input", prompt)

    delete_master_key_interactive()

    assert not prompt.called  # never asks for confirmation if there's nothing to delete


# ================================================================================
# get_master_key_interactive
# ================================================================================


@pytest.mark.unit
def test_resolve_interactive_choice_1_generates_and_stores_a_password(fake_keyring, monkeypatch):
    monkeypatch.setattr("builtins.input", Mock(return_value="1"))

    result = get_master_key_interactive()

    assert result
    assert fake_keyring[(SERVICE, USERNAME)] == result


@pytest.mark.unit
def test_resolve_interactive_choice_2_prompts_for_password(fake_keyring, monkeypatch):
    inputs = iter(["2", "real-pwd", ""])  # third value answers clear_terminal()'s confirm prompt
    monkeypatch.setattr("builtins.input", lambda *_: next(inputs))

    result = get_master_key_interactive()

    assert result == "real-pwd"
    assert fake_keyring[(SERVICE, USERNAME)] == "real-pwd"


@pytest.mark.unit
def test_resolve_interactive_choice_2_empty_password_cancels(fake_keyring, monkeypatch):
    inputs = iter(["2", ""])
    monkeypatch.setattr("builtins.input", lambda *_: next(inputs))

    result = get_master_key_interactive()

    assert result is None
    assert (SERVICE, USERNAME) not in fake_keyring


@pytest.mark.unit
def test_resolve_interactive_choice_3_cancels(fake_keyring, monkeypatch):
    monkeypatch.setattr("builtins.input", Mock(return_value="3"))

    result = get_master_key_interactive()

    assert result is None
    assert (SERVICE, USERNAME) not in fake_keyring


@pytest.mark.unit
def test_resolve_interactive_unrecognized_choice_cancels(fake_keyring, monkeypatch):
    monkeypatch.setattr("builtins.input", Mock(return_value="garbage"))

    result = get_master_key_interactive()

    assert result is None
    assert (SERVICE, USERNAME) not in fake_keyring


@pytest.mark.unit
def test_resolve_interactive_success_clears_the_terminal(fake_keyring, monkeypatch, mock_subprocess_run):
    monkeypatch.setattr("builtins.input", Mock(return_value="1"))

    get_master_key_interactive()

    mock_subprocess_run.assert_called_once()


@pytest.mark.unit
def test_resolve_interactive_cancel_does_not_clear_the_terminal(fake_keyring, monkeypatch, mock_subprocess_run):
    monkeypatch.setattr("builtins.input", Mock(return_value="3"))

    get_master_key_interactive()

    assert not mock_subprocess_run.called


# ================================================================================
# clear_terminal
# ================================================================================


@pytest.mark.unit
def test_clear_terminal_prompts_when_confirm_is_true(monkeypatch, mock_subprocess_run):
    prompt = Mock(return_value="")
    monkeypatch.setattr("builtins.input", prompt)

    clear_terminal()

    prompt.assert_called_once()
    mock_subprocess_run.assert_called_once()


@pytest.mark.unit
def test_clear_terminal_skips_prompt_when_confirm_is_false(monkeypatch, mock_subprocess_run):
    prompt = Mock()
    monkeypatch.setattr("builtins.input", prompt)

    clear_terminal(confirm=False)

    assert not prompt.called
    mock_subprocess_run.assert_called_once()


@pytest.mark.unit
def test_clear_terminal_uses_cls_on_windows(monkeypatch, mock_subprocess_run):
    monkeypatch.setattr("Pytre_Server.master_key.os.name", "nt")

    clear_terminal(confirm=False)

    mock_subprocess_run.assert_called_once_with(["cmd", "/c", "cls"])


@pytest.mark.unit
def test_clear_terminal_uses_clear_on_non_windows(monkeypatch, mock_subprocess_run):
    monkeypatch.setattr("Pytre_Server.master_key.os.name", "posix")

    clear_terminal(confirm=False)

    mock_subprocess_run.assert_called_once_with(["clear"])


# ================================================================================
# show_master_key_interactive
# ================================================================================


@pytest.mark.unit
def test_show_interactive_prints_stored_value(fake_keyring, capsys):
    fake_keyring[(SERVICE, USERNAME)] = "s3cret"

    show_master_key_interactive()

    assert "s3cret" in capsys.readouterr().out


@pytest.mark.unit
def test_show_interactive_prints_message_when_absent(fake_keyring, capsys):
    show_master_key_interactive()

    assert "No master password" in capsys.readouterr().out


# ================================================================================
# set_master_key_interactive
# ================================================================================


@pytest.mark.unit
def test_set_interactive_stores_confirmed_value(fake_keyring, monkeypatch):
    inputs = iter(["new-pwd", "y"])
    monkeypatch.setattr("builtins.input", lambda *_: next(inputs))

    set_master_key_interactive()

    assert fake_keyring[(SERVICE, USERNAME)] == "new-pwd"


@pytest.mark.unit
def test_set_interactive_accepts_uppercase_confirmation(fake_keyring, monkeypatch):
    inputs = iter(["new-pwd", "Y"])
    monkeypatch.setattr("builtins.input", lambda *_: next(inputs))

    set_master_key_interactive()

    assert fake_keyring[(SERVICE, USERNAME)] == "new-pwd"


@pytest.mark.unit
def test_set_interactive_cancels_on_empty_value(fake_keyring, monkeypatch):
    monkeypatch.setattr("builtins.input", Mock(return_value=""))

    set_master_key_interactive()

    assert (SERVICE, USERNAME) not in fake_keyring


@pytest.mark.unit
def test_set_interactive_cancels_when_not_confirmed(fake_keyring, monkeypatch):
    inputs = iter(["new-pwd", "n"])
    monkeypatch.setattr("builtins.input", lambda *_: next(inputs))

    set_master_key_interactive()

    assert (SERVICE, USERNAME) not in fake_keyring
