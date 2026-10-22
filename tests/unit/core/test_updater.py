"""
Tests for Pytre_Core/updater.py - finding, downloading and installing a release, without network:
httpx2.get / httpx2.stream are replaced by fakes returning real httpx2.Response objects
"""

import hashlib
import re
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import httpx2
import pytest

from Pytre_Core import updater as updater_module
from Pytre_Core.about import CLIENT_BASENAME, SERVER_BASENAME, AppStage, AppVersion
from Pytre_Core.updater import CHECKSUMS_ASSET, NEW_SUFFIX, OLD_SUFFIX, Release, Updater, UpdateError

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
EXE_CONTENT = b"new executable content"
CHECKSUMS_URL = "https://example.test/SHA256SUMS.txt"
EXE_URL = "https://example.test/Pytre_Client_2.0.0.exe"


# ================================================================================
# Fakes and helpers
# ================================================================================


def make_response(status: int = 200, url: str = "https://example.test", **kwargs) -> httpx2.Response:
    return httpx2.Response(status, request=httpx2.Request("GET", url), **kwargs)


def api_release(tag: str, basename: str = CLIENT_BASENAME, draft: bool = False, assets: bool = True) -> dict:
    """A release as returned by the GitHub API, with the assets published by the build workflow"""
    version = tag.removeprefix("v.")
    names = [f"{basename}_{version}.exe", CHECKSUMS_ASSET] if assets else []
    return {
        "tag_name": tag,
        "draft": draft,
        "html_url": f"https://github.com/Pytre/Pytre/releases/tag/{tag}",
        "assets": [
            {"name": name, "browser_download_url": f"https://example.test/{name}", "size": 1234} for name in names
        ],
    }


def updater_for(current: str, channel: AppStage = AppStage.RELEASE, basename: str = CLIENT_BASENAME) -> Updater:
    return Updater(basename, AppVersion.build_from_string(current), channel)


def found_version(updater: Updater, releases: list[dict], skipped: str | None = None) -> str | None:
    skipped_version = AppVersion.build_from_string(skipped) if skipped else None
    with patch.object(updater_module.httpx2, "get", return_value=make_response(json=releases)):
        release = updater.find_update(skipped=skipped_version)
    return str(release.version) if release else None


def make_release(content: bytes = EXE_CONTENT, size: int | None = None) -> Release:
    return Release(
        version=AppVersion.build_from_string("2.0.0"),
        page_url="https://github.com/Pytre/Pytre/releases/tag/v.2.0.0",
        exe_name="Pytre_Client_2.0.0.exe",
        exe_url=EXE_URL,
        exe_size=len(content) if size is None else size,
        checksums_url=CHECKSUMS_URL,
    )


def checksums_for(content: bytes, name: str = "Pytre_Client_2.0.0.exe") -> str:
    return f"{'0' * 64}  Pytre_Server_2.0.0.exe\n{hashlib.sha256(content).hexdigest()}  {name}\n"


@dataclass
class ServedFiles:
    """What the fake GitHub serves - tests change it to simulate bad hashes, chunks or HTTP errors"""

    checksums: str = checksums_for(EXE_CONTENT)
    exe_chunks: list[bytes] = field(default_factory=lambda: [EXE_CONTENT])
    exe_status: int = 200


@pytest.fixture
def fake_download(monkeypatch) -> ServedFiles:
    """Serves the checksums file through httpx2.get and the executable through httpx2.stream"""
    served = ServedFiles()

    def fake_get(url, **_kwargs):
        return make_response(text=served.checksums, url=url)

    @contextmanager
    def fake_stream(_method, url, **_kwargs):
        yield make_response(served.exe_status, url=url, content=iter(served.exe_chunks))

    monkeypatch.setattr(updater_module.httpx2, "get", fake_get)
    monkeypatch.setattr(updater_module.httpx2, "stream", fake_stream)
    return served


@pytest.fixture
def running_exe(tmp_path) -> Path:
    exe = tmp_path / "Pytre.exe"
    exe.write_bytes(b"current executable")
    return exe


# ================================================================================
# Finding an update
# ================================================================================


def test_release_channel_on_a_release_only_offers_releases():
    releases = [api_release("v.2.1.0-beta.1"), api_release("v.2.0.1"), api_release("v.2.0.0")]

    assert found_version(updater_for("2.0.0"), releases) == "2.0.1"


def test_newest_matching_release_is_returned_whatever_the_api_order():
    releases = [api_release("v.2.0.1"), api_release("v.2.0.3"), api_release("v.2.0.2")]

    assert found_version(updater_for("2.0.0"), releases) == "2.0.3"


def test_a_prerelease_keeps_receiving_prereleases_of_its_stage():
    releases = [api_release("v.2.0.0-beta.3"), api_release("v.2.0.0-alpha.9")]

    assert found_version(updater_for("2.0.0-beta.2"), releases) == "2.0.0-beta.3"


def test_a_prerelease_receives_the_release_once_published():
    releases = [api_release("v.2.0.0"), api_release("v.2.0.0-rc.1"), api_release("v.2.0.0-beta.3")]

    assert found_version(updater_for("2.0.0-beta.2"), releases) == "2.0.0"


def test_a_beta_channel_offers_betas_from_a_release():
    releases = [api_release("v.2.1.0-beta.1"), api_release("v.2.1.0-alpha.1")]

    assert found_version(updater_for("2.0.0", channel=AppStage.BETA), releases) == "2.1.0-beta.1"


def test_no_update_when_nothing_is_newer():
    releases = [api_release("v.2.0.0"), api_release("v.1.9.0")]

    assert found_version(updater_for("2.0.0"), releases) is None


def test_no_update_when_there_is_no_release_at_all():
    assert found_version(updater_for("2.0.0"), []) is None


def test_skipped_version_and_older_ones_are_not_offered():
    releases = [api_release("v.2.0.2"), api_release("v.2.0.1")]

    assert found_version(updater_for("2.0.0"), releases, skipped="2.0.2") is None
    assert found_version(updater_for("2.0.0"), releases, skipped="2.0.1") == "2.0.2"


def test_drafts_are_ignored():
    releases = [api_release("v.2.0.1", draft=True)]

    assert found_version(updater_for("2.0.0"), releases) is None


def test_tags_in_an_older_or_unknown_format_are_ignored():
    releases = [api_release("v.2.1.0_RC.1"), api_release("v.2.0.0_BETA.2"), api_release("not-a-version")]

    assert found_version(updater_for("1.9.0"), releases) is None


def test_releases_without_the_executable_or_checksums_are_ignored():
    without_checksums = api_release("v.2.0.2")
    without_checksums["assets"] = [a for a in without_checksums["assets"] if a["name"] != CHECKSUMS_ASSET]
    releases = [without_checksums, api_release("v.2.0.1", assets=False)]

    assert found_version(updater_for("2.0.0"), releases) is None


def test_only_the_executable_of_the_given_basename_is_looked_for():
    releases = [api_release("v.2.0.1", basename=SERVER_BASENAME)]

    assert found_version(updater_for("2.0.0", basename=CLIENT_BASENAME), releases) is None
    assert found_version(updater_for("2.0.0", basename=SERVER_BASENAME), releases) == "2.0.1"


def test_found_release_carries_its_assets():
    releases = [api_release("v.2.0.1")]
    with patch.object(updater_module.httpx2, "get", return_value=make_response(json=releases)):
        release = updater_for("2.0.0").find_update()

    assert release == Release(
        version=AppVersion((2, 0, 1)),
        page_url="https://github.com/Pytre/Pytre/releases/tag/v.2.0.1",
        exe_name="Pytre_Client_2.0.1.exe",
        exe_url="https://example.test/Pytre_Client_2.0.1.exe",
        exe_size=1234,
        checksums_url=f"https://example.test/{CHECKSUMS_ASSET}",
    )


def test_releases_are_fetched_from_the_configured_repository():
    with patch.object(updater_module.httpx2, "get", return_value=make_response(json=[])) as mock_get:
        Updater(CLIENT_BASENAME, AppVersion((2, 0, 0)), repo="Owner/Name").find_update()

    assert mock_get.call_args.args[0] == "https://api.github.com/repos/Owner/Name/releases"


@pytest.mark.parametrize(
    "response_or_error",
    [
        make_response(403, text="rate limit exceeded"),
        make_response(200, text="not json"),
        httpx2.ConnectError("unreachable"),
    ],
)
def test_unreachable_or_invalid_api_raises_update_error(response_or_error):
    kwargs = {"side_effect": response_or_error} if isinstance(response_or_error, Exception) else {}
    with patch.object(updater_module.httpx2, "get", return_value=response_or_error, **kwargs):
        with pytest.raises(UpdateError):
            updater_for("2.0.0").find_update()


# ================================================================================
# Expected hash
# ================================================================================


def test_expected_hash_is_read_from_the_checksums_line_of_the_executable():
    checksums = f"{'A' * 64}  Pytre_Server_2.0.0.exe\n{'B' * 64} *Pytre_Client_2.0.0.exe\n"
    with patch.object(updater_module.httpx2, "get", return_value=make_response(text=checksums)):
        assert make_release().retrieve_expected_hash() == "b" * 64


def test_expected_hash_missing_from_checksums_raises_update_error():
    checksums = f"{'A' * 64}  Pytre_Server_2.0.0.exe\n"
    with patch.object(updater_module.httpx2, "get", return_value=make_response(text=checksums)):
        with pytest.raises(UpdateError, match="missing from checksums"):
            make_release().retrieve_expected_hash()


# ================================================================================
# Downloading
# ================================================================================


def test_download_writes_the_verified_executable_next_to_the_target(fake_download, running_exe):
    new_file = make_release().download_exe(running_exe)

    assert new_file == running_exe.with_name(running_exe.name + NEW_SUFFIX)
    assert new_file.read_bytes() == EXE_CONTENT
    assert running_exe.read_bytes() == b"current executable"


def test_download_reports_its_progress(fake_download, running_exe):
    fake_download.exe_chunks = [EXE_CONTENT[:5], EXE_CONTENT[5:]]
    progress: list[tuple[int, int]] = []

    make_release().download_exe(running_exe, on_progress=lambda received, total: progress.append((received, total)))

    assert progress[-1] == (len(EXE_CONTENT), len(EXE_CONTENT))
    assert [received for received, _ in progress] == sorted(received for received, _ in progress)


def test_download_overwrites_a_leftover_partial_file(fake_download, running_exe):
    leftover = running_exe.with_name(running_exe.name + NEW_SUFFIX)
    leftover.write_bytes(b"partial")

    assert make_release().download_exe(running_exe).read_bytes() == EXE_CONTENT


def test_download_with_a_wrong_hash_raises_and_leaves_no_file(fake_download, running_exe):
    fake_download.checksums = checksums_for(b"another content")

    with pytest.raises(UpdateError, match="does not match"):
        make_release().download_exe(running_exe)
    assert not running_exe.with_name(running_exe.name + NEW_SUFFIX).exists()


def test_download_with_a_wrong_size_raises_and_leaves_no_file(fake_download, running_exe):
    with pytest.raises(UpdateError, match="Unexpected file bytes size"):
        make_release(size=len(EXE_CONTENT) + 1).download_exe(running_exe)
    assert not running_exe.with_name(running_exe.name + NEW_SUFFIX).exists()


def test_download_http_error_raises_update_error_and_leaves_no_file(fake_download, running_exe):
    fake_download.exe_status = 404

    with pytest.raises(UpdateError, match="Failed to download"):
        make_release().download_exe(running_exe)
    assert not running_exe.with_name(running_exe.name + NEW_SUFFIX).exists()


def test_download_network_error_on_checksums_raises_update_error(fake_download, running_exe):
    with patch.object(updater_module.httpx2, "get", side_effect=httpx2.ConnectError("unreachable")):
        with pytest.raises(UpdateError, match="Failed to download"):
            make_release().download_exe(running_exe)


def test_download_into_a_missing_folder_raises_update_error(fake_download, tmp_path):
    with pytest.raises(UpdateError, match="Failed to download"):
        make_release().download_exe(tmp_path / "missing" / "Pytre.exe")


# ================================================================================
# Installing
# ================================================================================


@pytest.fixture
def downloaded_exe(running_exe) -> Path:
    new_exe = running_exe.with_name(running_exe.name + NEW_SUFFIX)
    new_exe.write_bytes(EXE_CONTENT)
    return new_exe


def test_install_swaps_the_executables_keeping_the_name(running_exe, downloaded_exe):
    Updater.install(running_exe, downloaded_exe)

    assert running_exe.read_bytes() == EXE_CONTENT
    assert running_exe.with_name(running_exe.name + OLD_SUFFIX).read_bytes() == b"current executable"
    assert not downloaded_exe.exists()


def test_install_replaces_the_leftover_of_a_previous_update(running_exe, downloaded_exe):
    old_file = running_exe.with_name(running_exe.name + OLD_SUFFIX)
    old_file.write_bytes(b"executable of an older update")

    Updater.install(running_exe, downloaded_exe)

    assert old_file.read_bytes() == b"current executable"


def test_install_without_a_current_executable_raises_update_error(tmp_path, downloaded_exe):
    with pytest.raises(UpdateError, match="Failed to rename"):
        Updater.install(tmp_path / "missing.exe", downloaded_exe)


def _failing_rename(monkeypatch, failing_sources: set[str]):
    real_rename = Path.rename

    def rename(self, target):
        if self.name in failing_sources:
            raise PermissionError(f"{self.name} is locked")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", rename)


def test_install_rolls_back_when_the_new_executable_can_not_be_moved(monkeypatch, running_exe, downloaded_exe):
    _failing_rename(monkeypatch, {downloaded_exe.name})

    with pytest.raises(UpdateError, match="rolled back"):
        Updater.install(running_exe, downloaded_exe)

    assert running_exe.read_bytes() == b"current executable"
    assert not running_exe.with_name(running_exe.name + OLD_SUFFIX).exists()


def test_install_reports_a_failed_rollback(monkeypatch, running_exe, downloaded_exe):
    old_name = running_exe.name + OLD_SUFFIX
    _failing_rename(monkeypatch, {downloaded_exe.name, old_name})

    with pytest.raises(UpdateError, match="rollback failed"):
        Updater.install(running_exe, downloaded_exe)


def test_restart_starts_an_independent_instance(running_exe):
    with patch.object(updater_module.subprocess, "Popen") as mock_popen:
        Updater.restart(running_exe)

    args, kwargs = mock_popen.call_args
    assert args[0] == [str(running_exe)]
    assert kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_cleanup_previous_deletes_the_old_executable(running_exe):
    old_file = running_exe.with_name(running_exe.name + OLD_SUFFIX)
    old_file.write_bytes(b"previous")

    Updater.cleanup_previous(running_exe)

    assert not old_file.exists()
    assert running_exe.exists()


def test_cleanup_previous_without_old_executable_does_nothing(running_exe):
    Updater.cleanup_previous(running_exe)

    assert running_exe.exists()


def test_cleanup_previous_ignores_a_still_locked_file(monkeypatch, running_exe):
    running_exe.with_name(running_exe.name + OLD_SUFFIX).write_bytes(b"previous")

    def locked_unlink(_self, missing_ok=False):
        raise PermissionError("still in use")

    monkeypatch.setattr(Path, "unlink", locked_unlink)

    Updater.cleanup_previous(running_exe)  # must not raise


# ================================================================================
# Availability
# ================================================================================


@pytest.mark.parametrize(
    "frozen, key_available, expected",
    [(True, False, True), (True, True, False), (False, False, False), (False, True, False)],
)
def test_is_updatable_only_for_a_frozen_build_without_the_key(monkeypatch, frozen, key_available, expected):
    if frozen:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
    else:
        monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(updater_module, "secret_key_available", lambda: key_available)

    assert Updater.is_updatable() is expected


def test_current_exe_is_the_running_executable(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Pytre.exe"))

    assert Updater.current_exe() == tmp_path / "Pytre.exe"


def test_can_replace_exe_in_a_writable_folder(running_exe):
    assert Updater.can_replace_exe(running_exe) is True


def test_can_not_replace_exe_in_an_unusable_folder(tmp_path):
    assert Updater.can_replace_exe(tmp_path / "missing" / "Pytre.exe") is False


# ================================================================================
# Consistency with the build
# ================================================================================


def test_build_workflow_publishes_executables_under_the_about_basenames():
    """Updater looks for <basename>_<version>.exe: the workflow matrix must use the same names"""
    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    matrix_names = set(re.findall(r"^\s*- name: (\S+)\s*\n\s*spec: \S+", workflow, flags=re.MULTILINE))

    assert matrix_names == {CLIENT_BASENAME, SERVER_BASENAME}


@pytest.mark.parametrize(
    "spec_file, constant",
    [("Pytre_Client.spec", "CLIENT_BASENAME"), ("Pytre_Server.spec", "SERVER_BASENAME")],
)
def test_spec_files_name_executables_from_about(spec_file, constant):
    spec = (ROOT / spec_file).read_text(encoding="utf-8")

    assert f"name={constant}," in spec
