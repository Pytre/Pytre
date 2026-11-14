"""
Tests for Pytre_Core/policy.py - the enterprise policy file and the policies read from it.
"""

import json
import logging
import sys
from pathlib import Path

import pytest

from Pytre_Core.policy import POLICY_FILE, PlainHttpRemotesPolicy, UpdatePolicy, _Policy
from Pytre_Core.utils import LOGGER_ROOT_NAME

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
POLICIES = [UpdatePolicy, PlainHttpRemotesPolicy]


def write_policy(folder: Path, content: str, encoding: str = "utf-8") -> Path:
    policy_file = folder / POLICY_FILE
    policy_file.write_text(content, encoding=encoding)
    return policy_file


def warnings_logged(caplog) -> list[str]:
    return [r.message for r in caplog.records if r.levelno == logging.WARNING]


# ================================================================================
# Keys and values
# ================================================================================


@pytest.mark.parametrize(
    ("policy", "key"), [(UpdatePolicy, "updates"), (PlainHttpRemotesPolicy, "plain_http_remotes")]
)
def test_policy_key(policy, key):
    assert policy.policy_key() == key


def test_base_policy_has_no_key():
    with pytest.raises(NotImplementedError):
        _Policy.policy_key()


@pytest.mark.parametrize("policy", POLICIES)
def test_policy_is_ordered_from_least_to_most_restrictive(policy):
    assert list(policy) == [policy.ALLOWED, policy.DISABLED]
    assert policy.ALLOWED < policy.DISABLED


@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize(
    ("value", "name"), [("allowed", "ALLOWED"), ("DISABLED", "DISABLED"), ("Disabled", "DISABLED")]
)
def test_policy_from_value_ignores_case(policy, value, name):
    assert policy._from_value(value) is policy[name]


@pytest.mark.parametrize("policy", POLICIES)
def test_policy_from_value_rejects_an_unknown_name(policy):
    with pytest.raises(ValueError, match=f"Unknown {policy.policy_key()}: manual"):
        policy._from_value("manual")


# ================================================================================
# Loading from the file
# ================================================================================


@pytest.mark.parametrize("policy", POLICIES)
def test_policy_is_allowed_without_policy_file(tmp_path, caplog, policy):
    with caplog.at_level(logging.INFO, logger=LOGGER_ROOT_NAME):
        assert policy.load(tmp_path) is policy.ALLOWED

    assert caplog.records == []


@pytest.mark.parametrize("policy", POLICIES)
def test_policy_is_allowed_when_the_file_does_not_set_it(tmp_path, caplog, policy):
    write_policy(tmp_path, json.dumps({"other_setting": True}))

    with caplog.at_level(logging.INFO, logger=LOGGER_ROOT_NAME):
        assert policy.load(tmp_path) is policy.ALLOWED

    assert warnings_logged(caplog) == []
    assert any(r.message == f"No {policy.policy_key()} defined, allowed." for r in caplog.records)


@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize(
    ("value", "name"), [("allowed", "ALLOWED"), ("disabled", "DISABLED"), ("DISABLED", "DISABLED")]
)
def test_policy_reads_its_own_key_from_the_file(tmp_path, policy, value, name):
    write_policy(tmp_path, json.dumps({policy.policy_key(): value}))

    assert policy.load(tmp_path) is policy[name]


def test_each_policy_reads_only_its_own_key(tmp_path):
    write_policy(tmp_path, json.dumps({"updates": "disabled", "plain_http_remotes": "allowed"}))

    assert UpdatePolicy.load(tmp_path) is UpdatePolicy.DISABLED
    assert PlainHttpRemotesPolicy.load(tmp_path) is PlainHttpRemotesPolicy.ALLOWED


@pytest.mark.parametrize("policy", POLICIES)
def test_policy_logs_the_applied_policy_its_key_and_its_file(tmp_path, caplog, policy):
    policy_file = write_policy(tmp_path, json.dumps({policy.policy_key(): "disabled"}))

    with caplog.at_level(logging.INFO, logger=LOGGER_ROOT_NAME):
        policy.load(tmp_path)

    assert any(r.message == f"{policy.policy_key()}: disabled ({policy_file})" for r in caplog.records)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp1252"])
def test_policy_reads_files_in_the_usual_encodings(tmp_path, encoding):
    write_policy(tmp_path, '{"comment": "géré par le service informatique", "updates": "disabled"}', encoding)

    assert UpdatePolicy.load(tmp_path) is UpdatePolicy.DISABLED


@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize(
    "content",
    [
        pytest.param('{"KEY": "allowed"', id="invalid json"),
        pytest.param("", id="empty file"),
        pytest.param('["allowed"]', id="list root"),
        pytest.param('"allowed"', id="string root"),
        pytest.param('{"KEY": "manual"}', id="unknown value"),
        pytest.param('{"KEY": ""}', id="empty value"),
        pytest.param('{"KEY": true}', id="boolean value"),
        pytest.param('{"KEY": 1}', id="number value"),
        pytest.param('{"KEY": null}', id="null value"),
    ],
)
def test_policy_is_disabled_when_the_file_is_invalid(tmp_path, caplog, policy, content):
    """An invalid policy must never loosen anything, nor stop the client from starting"""
    write_policy(tmp_path, content.replace("KEY", policy.policy_key()))

    with caplog.at_level(logging.WARNING, logger=LOGGER_ROOT_NAME):
        assert policy.load(tmp_path) is policy.DISABLED

    expected = f"Invalid {policy.policy_key()} in {POLICY_FILE}"
    assert any(message.startswith(expected) for message in warnings_logged(caplog))


def test_an_unknown_value_only_disables_its_own_policy(tmp_path):
    write_policy(tmp_path, json.dumps({"updates": "manual"}))

    assert UpdatePolicy.load(tmp_path) is UpdatePolicy.DISABLED
    assert PlainHttpRemotesPolicy.load(tmp_path) is PlainHttpRemotesPolicy.ALLOWED


@pytest.mark.parametrize("policy", POLICIES)
def test_policy_is_disabled_when_the_file_can_not_be_read(tmp_path, monkeypatch, caplog, policy):
    write_policy(tmp_path, json.dumps({policy.policy_key(): "allowed"}))

    def denied_open(*args, **kwargs):
        raise PermissionError("access denied")

    monkeypatch.setattr("builtins.open", denied_open)

    with caplog.at_level(logging.WARNING, logger=LOGGER_ROOT_NAME):
        assert policy.load(tmp_path) is policy.DISABLED

    assert any("access denied" in message for message in warnings_logged(caplog))


@pytest.mark.parametrize("policy", POLICIES)
def test_policy_is_read_next_to_the_executable_by_default(tmp_path, monkeypatch, policy):
    """By default in the install folder, which is the executable's folder only once frozen"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Pytre_Client.exe"))
    write_policy(tmp_path, json.dumps({policy.policy_key(): "disabled"}))

    assert policy.load() is policy.DISABLED


# ================================================================================
# The policy file installed by the MSI
# ================================================================================


@pytest.mark.parametrize("policy", POLICIES)
def test_msi_policy_file_disables_every_policy(policy):
    """templates/Pytre.policy.json is installed by the MSI next to the client: a managed deployment."""
    assert policy.load(ROOT / "templates") is policy.DISABLED


def test_msi_policy_file_only_holds_known_keys():
    """A misspelled key would silently leave its policy allowed."""
    content = json.loads((ROOT / "templates" / POLICY_FILE).read_text(encoding="utf-8"))

    assert set(content) == {policy.policy_key() for policy in POLICIES}
