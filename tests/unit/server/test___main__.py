"""
Tests for Pytre_Server/__main__.py
"""

from unittest.mock import Mock

import logging

import pytest

from Pytre_Server.__main__ import (
    create_parser,
    hash_password_interactive,
    main,
    process_master_key_args,
    start_server,
)


@pytest.fixture(autouse=True)
def mock_logging_setup(monkeypatch):
    """Prevents real handlers from being attached to the shared "Pytre" logger during tests -
    setup_terminal_logging()/setup_file_logging() aren't idempotent (each call adds a new
    handler), so calling them for real here would leak state across the test session."""
    monkeypatch.setattr("Pytre_Server.__main__.setup_terminal_logging", Mock())
    monkeypatch.setattr("Pytre_Server.__main__.setup_file_logging", Mock())


# ================================================================================
# create_parser
# ================================================================================


@pytest.mark.unit
def test_master_key_flags_are_mutually_exclusive():
    parser = create_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--show-master-key", "--set-master-key"])


@pytest.mark.unit
def test_master_key_flags_default_to_false():
    parser = create_parser()

    args = parser.parse_args([])

    assert args.show_master_key is False
    assert args.set_master_key is False
    assert args.delete_master_key is False


@pytest.mark.unit
def test_hash_password_flag_is_mutually_exclusive_with_master_key_flags():
    parser = create_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--hash-password", "--show-master-key"])


@pytest.mark.unit
def test_forwarded_allow_ips_defaults_to_none():
    """None = leave uvicorn's own default ($FORWARDED_ALLOW_IPS or 127.0.0.1) untouched."""
    assert create_parser().parse_args([]).forwarded_allow_ips is None


@pytest.mark.unit
def test_forwarded_allow_ips_is_parsed():
    args = create_parser().parse_args(["--forwarded-allow-ips", "10.0.0.5"])

    assert args.forwarded_allow_ips == "10.0.0.5"


@pytest.mark.unit
def test_hash_password_flag_defaults_to_false():
    parser = create_parser()

    args = parser.parse_args([])

    assert args.hash_password is False


# ================================================================================
# process_master_key_args
# ================================================================================


@pytest.mark.unit
def test_process_master_key_args_show(monkeypatch):
    show = Mock()
    monkeypatch.setattr("Pytre_Server.__main__.show_master_key_interactive", show)
    args = create_parser().parse_args(["--show-master-key"])

    result = process_master_key_args(args)

    assert result is True
    show.assert_called_once()


@pytest.mark.unit
def test_process_master_key_args_set(monkeypatch):
    set_key = Mock()
    monkeypatch.setattr("Pytre_Server.__main__.set_master_key_interactive", set_key)
    args = create_parser().parse_args(["--set-master-key"])

    result = process_master_key_args(args)

    assert result is True
    set_key.assert_called_once()


@pytest.mark.unit
def test_process_master_key_args_delete(monkeypatch):
    delete = Mock()
    monkeypatch.setattr("Pytre_Server.__main__.delete_master_key_interactive", delete)
    args = create_parser().parse_args(["--delete-master-key"])

    result = process_master_key_args(args)

    assert result is True
    delete.assert_called_once()


@pytest.mark.unit
def test_process_master_key_args_none(monkeypatch):
    show = Mock()
    set_key = Mock()
    delete = Mock()
    monkeypatch.setattr("Pytre_Server.__main__.show_master_key_interactive", show)
    monkeypatch.setattr("Pytre_Server.__main__.set_master_key_interactive", set_key)
    monkeypatch.setattr("Pytre_Server.__main__.delete_master_key_interactive", delete)
    args = create_parser().parse_args([])

    result = process_master_key_args(args)

    assert result is False
    assert not show.called
    assert not set_key.called
    assert not delete.called


# ================================================================================
# hash_password_interactive
# ================================================================================


@pytest.mark.unit
def test_hash_password_interactive_prints_hash_on_matching_passwords(monkeypatch, capsys):
    inputs = iter(["s3cret", "s3cret"])
    monkeypatch.setattr("Pytre_Server.__main__.getpass.getpass", lambda *_: next(inputs))
    hash_mock = Mock(return_value="hashed-value")
    monkeypatch.setattr("Pytre_Server.__main__.hash_password", hash_mock)

    hash_password_interactive()

    hash_mock.assert_called_once_with("s3cret")
    assert "hashed-value" in capsys.readouterr().out


@pytest.mark.unit
def test_hash_password_interactive_cancels_on_mismatched_passwords(monkeypatch, capsys):
    inputs = iter(["s3cret", "different"])
    monkeypatch.setattr("Pytre_Server.__main__.getpass.getpass", lambda *_: next(inputs))
    hash_mock = Mock()
    monkeypatch.setattr("Pytre_Server.__main__.hash_password", hash_mock)

    hash_password_interactive()

    assert not hash_mock.called
    assert "do not match" in capsys.readouterr().out


# ================================================================================
# start_server
# ================================================================================


@pytest.mark.unit
def test_start_server_runs_uvicorn_with_resolved_master_key(monkeypatch):
    monkeypatch.setattr("Pytre_Server.__main__.get_master_key_interactive", Mock(return_value="s3cret"))
    create_app_mock = Mock(return_value="app")
    monkeypatch.setattr("Pytre_Server.__main__.create_app", create_app_mock)
    run = Mock()
    monkeypatch.setattr("Pytre_Server.__main__.uvicorn.run", run)
    args = create_parser().parse_args(["--host", "0.0.0.0", "--port", "9000"])

    start_server(args)

    create_app_mock.assert_called_once_with(
        master_key="s3cret", chunk_size=2000, max_concurrency_per_user=4, heartbeat_secs=30
    )
    run.assert_called_once_with("app", host="0.0.0.0", port=9000, forwarded_allow_ips=None)


@pytest.mark.unit
def test_start_server_forwards_forwarded_allow_ips_to_uvicorn(monkeypatch):
    monkeypatch.setattr("Pytre_Server.__main__.get_master_key_interactive", Mock(return_value="s3cret"))
    monkeypatch.setattr("Pytre_Server.__main__.create_app", Mock(return_value="app"))
    run = Mock()
    monkeypatch.setattr("Pytre_Server.__main__.uvicorn.run", run)
    args = create_parser().parse_args(["--forwarded-allow-ips", "10.0.0.5,10.0.0.6"])

    start_server(args)

    assert run.call_args.kwargs["forwarded_allow_ips"] == "10.0.0.5,10.0.0.6"


@pytest.mark.unit
def test_start_server_warns_on_wildcard_forwarded_allow_ips(monkeypatch):
    monkeypatch.setattr("Pytre_Server.__main__.get_master_key_interactive", Mock(return_value="s3cret"))
    monkeypatch.setattr("Pytre_Server.__main__.create_app", Mock(return_value="app"))
    monkeypatch.setattr("Pytre_Server.__main__.uvicorn.run", Mock())
    log_mock = Mock()
    monkeypatch.setattr("Pytre_Server.__main__.log", log_mock)
    args = create_parser().parse_args(["--forwarded-allow-ips", "*"])

    start_server(args)

    assert log_mock.called
    assert log_mock.call_args.args[1] == logging.WARNING


@pytest.mark.unit
@pytest.mark.parametrize("cli_args", [[], ["--forwarded-allow-ips", "10.0.0.5"]])
def test_start_server_does_not_warn_without_wildcard(monkeypatch, cli_args):
    monkeypatch.setattr("Pytre_Server.__main__.get_master_key_interactive", Mock(return_value="s3cret"))
    monkeypatch.setattr("Pytre_Server.__main__.create_app", Mock(return_value="app"))
    monkeypatch.setattr("Pytre_Server.__main__.uvicorn.run", Mock())
    log_mock = Mock()
    monkeypatch.setattr("Pytre_Server.__main__.log", log_mock)
    args = create_parser().parse_args(cli_args)

    start_server(args)

    assert not log_mock.called


@pytest.mark.unit
def test_start_server_cancelled_when_no_master_key(monkeypatch):
    monkeypatch.setattr("Pytre_Server.__main__.get_master_key_interactive", Mock(return_value=None))
    run = Mock()
    monkeypatch.setattr("Pytre_Server.__main__.uvicorn.run", run)
    args = create_parser().parse_args([])

    start_server(args)

    assert not run.called


# ================================================================================
# main
# ================================================================================


@pytest.mark.unit
def test_main_dispatches_to_master_key_flag_and_skips_server_start(monkeypatch):
    monkeypatch.setattr("sys.argv", ["prog", "--show-master-key"])
    monkeypatch.setattr("Pytre_Server.__main__.show_master_key_interactive", Mock())
    start = Mock()
    monkeypatch.setattr("Pytre_Server.__main__.start_server", start)

    main()

    assert not start.called


@pytest.mark.unit
def test_main_starts_server_when_no_master_key_flag(monkeypatch):
    monkeypatch.setattr("sys.argv", ["prog"])
    start = Mock()
    monkeypatch.setattr("Pytre_Server.__main__.start_server", start)

    main()

    start.assert_called_once()


@pytest.mark.unit
def test_main_dispatches_to_hash_password_flag_and_skips_server_start(monkeypatch):
    monkeypatch.setattr("sys.argv", ["prog", "--hash-password"])
    monkeypatch.setattr("Pytre_Server.__main__.hash_password_interactive", Mock())
    start = Mock()
    monkeypatch.setattr("Pytre_Server.__main__.start_server", start)

    main()

    assert not start.called

