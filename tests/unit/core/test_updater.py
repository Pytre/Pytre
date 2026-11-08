"""
Tests for Pytre_Core/updater.py - finding, downloading, extracting (onedir build) and installing a release, without network:
httpx2.get / httpx2.stream are replaced by fakes returning real httpx2.Response objects
"""

import hashlib
import json
import logging
import re
import shutil
import sys
import zipfile
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import httpx2
import pytest

from Pytre_Core import updater as updater_module
from Pytre_Core.about import CLIENT_BASENAME, SERVER_BASENAME, AppStage, AppVersion, expected_git_tag, full_version
from Pytre_Core.updater import (
    CHECKSUMS_ASSET,
    CONTENTS_PREFIX,
    DOWNLOAD_SUFFIX,
    NEW_SUFFIX,
    OLD_SUFFIX,
    POLICY_FILE,
    Release,
    Updater,
    UpdateError,
    UpdatePolicy,
)
from Pytre_Core.utils import LOGGER_ROOT_NAME

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


def api_release(
    tag: str,
    basename: str = CLIENT_BASENAME,
    draft: bool = False,
    assets: bool = True,
    formats: tuple[str, ...] = ("exe", "zip"),
) -> dict:
    """A release as returned by the GitHub API, with the assets published by the build workflow"""
    version = tag.removeprefix("v.")
    names = [f"{basename}_{version}.{extension}" for extension in formats] + [CHECKSUMS_ASSET] if assets else []
    return {
        "tag_name": tag,
        "draft": draft,
        "html_url": f"https://github.com/Pytre/Pytre/releases/tag/{tag}",
        "assets": [
            {"name": name, "browser_download_url": f"https://example.test/{name}", "size": 1234} for name in names
        ],
    }


@pytest.fixture
def updater_for(monkeypatch) -> Callable[..., Updater]:
    """Builds an Updater for a running build of version `current`, a single executable unless `onedir`"""

    def build(
        current: str, channel: AppStage = AppStage.RELEASE, basename: str = CLIENT_BASENAME, onedir: bool = False
    ) -> Updater:
        running = AppVersion.build_from_string(current)
        monkeypatch.setattr(AppVersion, "current", classmethod(lambda _cls: running))
        monkeypatch.setattr(Updater, "is_onedir", staticmethod(lambda: onedir))
        return Updater(basename, channel)

    return build


def found_version(updater: Updater, releases: list[dict], skipped: str | None = None) -> str | None:
    skipped_version = AppVersion.build_from_string(skipped) if skipped else None
    with patch.object(updater_module.httpx2, "get", return_value=make_response(json=releases)):
        release = updater.find_update(skipped=skipped_version)
    return str(release.version) if release else None


def make_release(
    content: bytes = EXE_CONTENT, size: int | None = None, onedir: bool = False, version: str = "2.0.0"
) -> Release:
    return Release(
        version=AppVersion.build_from_string(version),
        page_url=f"https://github.com/Pytre/Pytre/releases/tag/v.{version}",
        basename=CLIENT_BASENAME,
        asset_name=f"{CLIENT_BASENAME}_{version}.{'zip' if onedir else 'exe'}",
        asset_url=EXE_URL,
        asset_size=len(content) if size is None else size,
        checksums_url=CHECKSUMS_URL,
        onedir=onedir,
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


def test_release_channel_on_a_release_only_offers_releases(updater_for):
    releases = [api_release("v.2.1.0-beta.1"), api_release("v.2.0.1"), api_release("v.2.0.0")]

    assert found_version(updater_for("2.0.0"), releases) == "2.0.1"


def test_newest_matching_release_is_returned_whatever_the_api_order(updater_for):
    releases = [api_release("v.2.0.1"), api_release("v.2.0.3"), api_release("v.2.0.2")]

    assert found_version(updater_for("2.0.0"), releases) == "2.0.3"


def test_a_prerelease_keeps_receiving_prereleases_of_its_stage(updater_for):
    releases = [api_release("v.2.0.0-beta.3"), api_release("v.2.0.0-alpha.9")]

    assert found_version(updater_for("2.0.0-beta.2"), releases) == "2.0.0-beta.3"


def test_a_prerelease_receives_the_release_once_published(updater_for):
    releases = [api_release("v.2.0.0"), api_release("v.2.0.0-rc.1"), api_release("v.2.0.0-beta.3")]

    assert found_version(updater_for("2.0.0-beta.2"), releases) == "2.0.0"


def test_a_beta_channel_offers_betas_from_a_release(updater_for):
    releases = [api_release("v.2.1.0-beta.1"), api_release("v.2.1.0-alpha.1")]

    assert found_version(updater_for("2.0.0", channel=AppStage.BETA), releases) == "2.1.0-beta.1"


def test_no_update_when_nothing_is_newer(updater_for):
    releases = [api_release("v.2.0.0"), api_release("v.1.9.0")]

    assert found_version(updater_for("2.0.0"), releases) is None


def test_no_update_when_there_is_no_release_at_all(updater_for):
    assert found_version(updater_for("2.0.0"), []) is None


def test_skipped_version_and_older_ones_are_not_offered(updater_for):
    releases = [api_release("v.2.0.2"), api_release("v.2.0.1")]

    assert found_version(updater_for("2.0.0"), releases, skipped="2.0.2") is None
    assert found_version(updater_for("2.0.0"), releases, skipped="2.0.1") == "2.0.2"


def test_drafts_are_ignored(updater_for):
    releases = [api_release("v.2.0.1", draft=True)]

    assert found_version(updater_for("2.0.0"), releases) is None


def test_tags_in_an_older_or_unknown_format_are_ignored(updater_for):
    releases = [api_release("v.2.1.0_RC.1"), api_release("v.2.0.0_BETA.2"), api_release("not-a-version")]

    assert found_version(updater_for("1.9.0"), releases) is None


def test_releases_without_the_executable_or_checksums_are_ignored(updater_for):
    without_checksums = api_release("v.2.0.2")
    without_checksums["assets"] = [a for a in without_checksums["assets"] if a["name"] != CHECKSUMS_ASSET]
    releases = [without_checksums, api_release("v.2.0.1", assets=False)]

    assert found_version(updater_for("2.0.0"), releases) is None


def test_only_the_executable_of_the_given_basename_is_looked_for(updater_for):
    releases = [api_release("v.2.0.1", basename=SERVER_BASENAME)]

    assert found_version(updater_for("2.0.0", basename=CLIENT_BASENAME), releases) is None
    assert found_version(updater_for("2.0.0", basename=SERVER_BASENAME), releases) == "2.0.1"


@pytest.mark.parametrize("onedir, extension", [(False, "exe"), (True, "zip")], ids=["onefile", "onedir"])
def test_found_release_carries_the_asset_of_the_running_build(updater_for, onedir, extension):
    """A single executable is replaced by the release executable, a folder build by the release zip"""
    releases = [api_release("v.2.0.1")]
    with patch.object(updater_module.httpx2, "get", return_value=make_response(json=releases)):
        release = updater_for("2.0.0", onedir=onedir).find_update()

    assert release == Release(
        version=AppVersion((2, 0, 1)),
        page_url="https://github.com/Pytre/Pytre/releases/tag/v.2.0.1",
        basename=CLIENT_BASENAME,
        asset_name=f"Pytre_Client_2.0.1.{extension}",
        asset_url=f"https://example.test/Pytre_Client_2.0.1.{extension}",
        asset_size=1234,
        checksums_url=f"https://example.test/{CHECKSUMS_ASSET}",
        onedir=onedir,
    )


@pytest.mark.parametrize("onedir, published", [(False, ("zip",)), (True, ("exe",))], ids=["onefile", "onedir"])
def test_releases_without_the_asset_of_the_running_build_are_ignored(updater_for, onedir, published):
    releases = [api_release("v.2.0.1", formats=published)]

    assert found_version(updater_for("2.0.0", onedir=onedir), releases) is None


def test_updater_looks_for_updates_of_the_running_version(monkeypatch):
    running = AppVersion((2, 0, 0), AppStage.BETA, 4)
    monkeypatch.setattr(AppVersion, "current", classmethod(lambda _cls: running))

    assert Updater(CLIENT_BASENAME).version == running


def test_releases_are_fetched_from_the_configured_repository():
    with patch.object(updater_module.httpx2, "get", return_value=make_response(json=[])) as mock_get:
        Updater(CLIENT_BASENAME, repo="Owner/Name").find_update()

    assert mock_get.call_args.args[0] == "https://api.github.com/repos/Owner/Name/releases"


@pytest.mark.parametrize(
    "response_or_error",
    [
        make_response(403, text="rate limit exceeded"),
        make_response(200, text="not json"),
        httpx2.ConnectError("unreachable"),
    ],
)
def test_unreachable_or_invalid_api_raises_update_error(updater_for, response_or_error):
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
    new_file = make_release().download_update(running_exe)

    assert new_file == running_exe.with_name(running_exe.name + NEW_SUFFIX)
    assert new_file.read_bytes() == EXE_CONTENT
    assert running_exe.read_bytes() == b"current executable"


def test_download_reports_its_progress(fake_download, running_exe):
    fake_download.exe_chunks = [EXE_CONTENT[:5], EXE_CONTENT[5:]]
    progress: list[tuple[int, int]] = []

    make_release().download_update(running_exe, on_progress=lambda received, total: progress.append((received, total)))

    assert progress[-1] == (len(EXE_CONTENT), len(EXE_CONTENT))
    assert [received for received, _ in progress] == sorted(received for received, _ in progress)


def test_download_overwrites_a_leftover_partial_file(fake_download, running_exe):
    leftover = running_exe.with_name(running_exe.name + NEW_SUFFIX)
    leftover.write_bytes(b"partial")

    assert make_release().download_update(running_exe).read_bytes() == EXE_CONTENT


def test_download_with_a_wrong_hash_raises_and_leaves_no_file(fake_download, running_exe):
    fake_download.checksums = checksums_for(b"another content")

    with pytest.raises(UpdateError, match="does not match"):
        make_release().download_update(running_exe)
    assert not running_exe.with_name(running_exe.name + NEW_SUFFIX).exists()


def test_download_with_a_wrong_size_raises_and_leaves_no_file(fake_download, running_exe):
    with pytest.raises(UpdateError, match="Unexpected file bytes size"):
        make_release(size=len(EXE_CONTENT) + 1).download_update(running_exe)
    assert not running_exe.with_name(running_exe.name + NEW_SUFFIX).exists()


def test_download_http_error_raises_update_error_and_leaves_no_file(fake_download, running_exe):
    fake_download.exe_status = 404

    with pytest.raises(UpdateError, match="Failed to download"):
        make_release().download_update(running_exe)
    assert not running_exe.with_name(running_exe.name + NEW_SUFFIX).exists()


def test_download_network_error_on_checksums_raises_update_error(fake_download, running_exe):
    with patch.object(updater_module.httpx2, "get", side_effect=httpx2.ConnectError("unreachable")):
        with pytest.raises(UpdateError, match="Failed to download"):
            make_release().download_update(running_exe)


def test_download_into_a_missing_folder_raises_update_error(fake_download, tmp_path):
    with pytest.raises(UpdateError, match="Failed to download"):
        make_release().download_update(tmp_path / "missing" / "Pytre.exe")


# ================================================================================
# Onedir build - extracting the release zip next to the running version
# ================================================================================

ONEDIR_EXE_CONTENT = b"new onedir executable"
NEW_VERSION = AppVersion((2, 0, 1))


@pytest.fixture
def onedir_exe(tmp_path) -> Path:
    """Running onedir build of version 2.0.0: its executable and contents folder"""
    exe = tmp_path / "app" / f"{CLIENT_BASENAME}.exe"
    contents = exe.parent / f"{CONTENTS_PREFIX}2.0.0"
    contents.mkdir(parents=True)
    (contents / "python314.dll").write_bytes(b"running dll")
    exe.write_bytes(b"current executable")
    return exe


@pytest.fixture
def downloads(tmp_path) -> Path:
    folder = tmp_path / "downloads"
    folder.mkdir()
    return folder


def make_onedir_zip(
    folder: Path, version: str = "2.0.1", with_exe: bool = True, extra: dict[str, bytes] | None = None
) -> Path:
    """Zip of a onedir build made like the build workflow does, with its `<basename>/` root folder"""
    build = folder / "build" / CLIENT_BASENAME
    contents = build / f"{CONTENTS_PREFIX}{version}"
    (contents / "res").mkdir(parents=True)
    (contents / "python314.dll").write_bytes(b"dll")
    (contents / "res" / "app.ico").write_bytes(b"icon")
    if with_exe:
        (build / f"{CLIENT_BASENAME}.exe").write_bytes(ONEDIR_EXE_CONTENT)

    archive_base = str(folder / f"{CLIENT_BASENAME}_{version}")
    zip_file = Path(shutil.make_archive(archive_base, "zip", root_dir=build.parent, base_dir=CLIENT_BASENAME))
    if extra:
        with zipfile.ZipFile(zip_file, "a") as archive:
            for name, content in extra.items():
                archive.writestr(name, content)
    return zip_file


def extracted_paths(onedir_exe: Path) -> tuple[Path, Path]:
    """Contents folder and executable of version 2.0.1 once extracted next to `onedir_exe`"""
    return onedir_exe.parent / f"{CONTENTS_PREFIX}2.0.1", onedir_exe.with_name(onedir_exe.name + NEW_SUFFIX)


def assert_nothing_extracted(onedir_exe: Path) -> None:
    contents, new_exe = extracted_paths(onedir_exe)
    assert not contents.exists()
    assert not new_exe.exists()
    assert onedir_exe.read_bytes() == b"current executable"
    assert (onedir_exe.parent / f"{CONTENTS_PREFIX}2.0.0" / "python314.dll").read_bytes() == b"running dll"


def locked_rmtree(_path, *_args, **_kwargs):
    raise PermissionError("file in use")


def test_extract_onedir_puts_the_new_version_next_to_the_running_one(onedir_exe, downloads):
    new_exe = Updater.extract_onedir(make_onedir_zip(downloads), onedir_exe, CLIENT_BASENAME, NEW_VERSION)

    contents, expected_exe = extracted_paths(onedir_exe)
    assert new_exe == expected_exe
    assert new_exe.read_bytes() == ONEDIR_EXE_CONTENT
    assert (contents / "python314.dll").read_bytes() == b"dll"
    assert (contents / "res" / "app.ico").read_bytes() == b"icon"
    assert onedir_exe.read_bytes() == b"current executable"
    assert (onedir_exe.parent / f"{CONTENTS_PREFIX}2.0.0" / "python314.dll").read_bytes() == b"running dll"


@pytest.mark.parametrize(
    "entry",
    [f"{CLIENT_BASENAME}/{POLICY_FILE}", "other/readme.txt", f"{CLIENT_BASENAME}/{CONTENTS_PREFIX}1.0.0/old.dll"],
    ids=["file next to the executable", "other root folder", "contents folder of another version"],
)
def test_extract_onedir_refuses_an_unexpected_entry(onedir_exe, downloads, entry):
    zip_file = make_onedir_zip(downloads, extra={entry: b"unexpected"})

    with pytest.raises(UpdateError, match="Unexpected entry"):
        Updater.extract_onedir(zip_file, onedir_exe, CLIENT_BASENAME, NEW_VERSION)
    assert_nothing_extracted(onedir_exe)


def test_extract_onedir_refuses_an_entry_leaving_its_folder(onedir_exe, downloads, tmp_path):
    """Entries extracted before it are removed too"""
    zip_file = make_onedir_zip(downloads, extra={f"{CLIENT_BASENAME}/{CONTENTS_PREFIX}2.0.1/../../evil.txt": b"!"})

    with pytest.raises(UpdateError, match="leaving its folder"):
        Updater.extract_onedir(zip_file, onedir_exe, CLIENT_BASENAME, NEW_VERSION)
    assert not (tmp_path / "evil.txt").exists()
    assert_nothing_extracted(onedir_exe)


def test_extract_onedir_without_the_executable_raises_update_error(onedir_exe, downloads):
    zip_file = make_onedir_zip(downloads, with_exe=False)

    with pytest.raises(UpdateError, match="missing from"):
        Updater.extract_onedir(zip_file, onedir_exe, CLIENT_BASENAME, NEW_VERSION)
    assert_nothing_extracted(onedir_exe)


def test_extract_onedir_of_a_corrupted_zip_raises_update_error(onedir_exe, downloads):
    zip_file = downloads / "Pytre_Client_2.0.1.zip"
    zip_file.write_bytes(b"not a zip")

    with pytest.raises(UpdateError, match="Failed to extract"):
        Updater.extract_onedir(zip_file, onedir_exe, CLIENT_BASENAME, NEW_VERSION)
    assert_nothing_extracted(onedir_exe)


def test_extract_onedir_failing_on_the_executable_removes_the_new_contents_folder(monkeypatch, onedir_exe, downloads):
    zip_file = make_onedir_zip(downloads)
    real_copy = shutil.copyfileobj

    def copy(source, dest, *args, **kwargs):
        if str(dest.name).endswith(NEW_SUFFIX):
            raise OSError("disk full")
        return real_copy(source, dest, *args, **kwargs)

    monkeypatch.setattr(updater_module.shutil, "copyfileobj", copy)

    with pytest.raises(UpdateError, match="disk full"):
        Updater.extract_onedir(zip_file, onedir_exe, CLIENT_BASENAME, NEW_VERSION)
    assert_nothing_extracted(onedir_exe)


def test_extract_onedir_replaces_the_leftovers_of_a_failed_attempt(onedir_exe, downloads):
    contents, new_exe = extracted_paths(onedir_exe)
    contents.mkdir()
    (contents / "stale.dll").write_bytes(b"stale")
    new_exe.write_bytes(b"partial")

    Updater.extract_onedir(make_onedir_zip(downloads), onedir_exe, CLIENT_BASENAME, NEW_VERSION)

    assert not (contents / "stale.dll").exists()
    assert (contents / "python314.dll").read_bytes() == b"dll"
    assert new_exe.read_bytes() == ONEDIR_EXE_CONTENT


def test_extract_onedir_stops_when_the_leftovers_can_not_be_deleted(monkeypatch, onedir_exe, downloads):
    contents, new_exe = extracted_paths(onedir_exe)
    contents.mkdir()
    zip_file = make_onedir_zip(downloads)
    monkeypatch.setattr(updater_module.shutil, "rmtree", locked_rmtree)

    with pytest.raises(UpdateError, match="leftovers"):
        Updater.extract_onedir(zip_file, onedir_exe, CLIENT_BASENAME, NEW_VERSION)
    assert not new_exe.exists()


def test_download_update_of_a_onedir_build_extracts_the_zip_then_deletes_it(fake_download, onedir_exe, downloads):
    zip_content = make_onedir_zip(downloads).read_bytes()
    release = make_release(zip_content, onedir=True, version="2.0.1")
    fake_download.exe_chunks = [zip_content]
    fake_download.checksums = checksums_for(zip_content, release.asset_name)

    new_exe = release.download_update(onedir_exe)

    contents, expected_exe = extracted_paths(onedir_exe)
    assert new_exe == expected_exe
    assert new_exe.read_bytes() == ONEDIR_EXE_CONTENT
    assert (contents / "python314.dll").is_file()
    assert not onedir_exe.with_name(release.asset_name + DOWNLOAD_SUFFIX).exists()


def test_download_update_of_a_onedir_build_failing_to_extract_leaves_no_file(fake_download, onedir_exe):
    content = b"not a zip"
    release = make_release(content, onedir=True, version="2.0.1")
    fake_download.exe_chunks = [content]
    fake_download.checksums = checksums_for(content, release.asset_name)

    with pytest.raises(UpdateError, match="Failed to extract"):
        release.download_update(onedir_exe)
    assert sorted(path.name for path in onedir_exe.parent.iterdir()) == [
        f"{CLIENT_BASENAME}.exe",
        f"{CONTENTS_PREFIX}2.0.0",
    ]


def test_cleanup_contents_folders_deletes_only_the_older_versions(onedir_exe):
    """The running version, a newer one (maybe being extracted), a name without version and a file are kept"""
    install = onedir_exe.parent
    for version in ("1.9.0", "2.0.0-beta.4", "2.0.1", "custom"):
        (install / f"{CONTENTS_PREFIX}{version}").mkdir()
    (install / f"{CONTENTS_PREFIX}1.0.0").write_bytes(b"a file, not a folder")

    Updater.cleanup_contents_folders(onedir_exe, AppVersion((2, 0, 0)))

    kept = [f"{CONTENTS_PREFIX}{version}" for version in ("1.0.0", "2.0.0", "2.0.1", "custom")]
    assert sorted(path.name for path in install.iterdir()) == sorted([onedir_exe.name, *kept])


def test_cleanup_contents_folders_ignores_a_folder_still_in_use(monkeypatch, onedir_exe):
    older = onedir_exe.parent / f"{CONTENTS_PREFIX}1.9.0"
    older.mkdir()
    monkeypatch.setattr(updater_module.shutil, "rmtree", locked_rmtree)

    Updater.cleanup_contents_folders(onedir_exe, AppVersion((2, 0, 0)))  # must not raise

    assert older.is_dir()


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

    assert Updater.cleanup_previous(running_exe) is True

    assert not old_file.exists()
    assert running_exe.exists()


def test_cleanup_previous_without_old_executable_does_nothing(running_exe):
    assert Updater.cleanup_previous(running_exe) is True

    assert running_exe.exists()


def test_cleanup_previous_ignores_a_still_locked_file(monkeypatch, running_exe):
    running_exe.with_name(running_exe.name + OLD_SUFFIX).write_bytes(b"previous")

    def locked_unlink(_self, missing_ok=False):
        raise PermissionError("still in use")

    monkeypatch.setattr(Path, "unlink", locked_unlink)

    assert Updater.cleanup_previous(running_exe) is False  # must not raise: a previous version is still running


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
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(updater_module, "secret_key_available", lambda: key_available)

    assert Updater.is_updatable() is expected


@pytest.mark.parametrize(
    "bundle, expected",
    [
        pytest.param("app/_internal_2.0.0", True, id="onedir versioned contents folder"),
        pytest.param("app", True, id="onedir contents in the executable folder"),
        pytest.param("temp/_MEI12345", False, id="onefile temporary folder"),
        pytest.param(None, False, id="not frozen"),
    ],
)
def test_is_onedir_when_the_bundle_sits_in_the_executable_folder(monkeypatch, tmp_path, bundle, expected):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app" / "Pytre_Client.exe"))
    if bundle is None:
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    else:
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / bundle), raising=False)

    assert Updater.is_onedir() is expected


def test_onedir_build_is_updatable_without_the_key(monkeypatch, tmp_path):
    """The zip updates itself like the executable - a deployment turns it off with the policy file"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Pytre_Client.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "_internal_2.0.0"), raising=False)
    monkeypatch.setattr(updater_module, "secret_key_available", lambda: False)

    assert Updater.is_updatable() is True


def test_current_exe_is_the_running_executable(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Pytre.exe"))

    assert Updater.current_exe() == tmp_path / "Pytre.exe"


def test_can_replace_exe_in_a_writable_folder(running_exe):
    assert Updater.can_replace_exe(running_exe) is True


def test_can_not_replace_exe_in_an_unusable_folder(tmp_path):
    assert Updater.can_replace_exe(tmp_path / "missing" / "Pytre.exe") is False


# ================================================================================
# Enterprise update policy
# ================================================================================


def write_policy(folder: Path, content: str, encoding: str = "utf-8") -> Path:
    policy_file = folder / POLICY_FILE
    policy_file.write_text(content, encoding=encoding)
    return policy_file


def warnings_logged(caplog) -> list[str]:
    return [r.message for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.parametrize(
    "value, expected",
    [("allowed", UpdatePolicy.ALLOWED), ("DISABLED", UpdatePolicy.DISABLED), ("Disabled", UpdatePolicy.DISABLED)],
)
def test_policy_from_value_ignores_case(value, expected):
    assert UpdatePolicy.from_value(value) is expected


def test_policy_from_value_rejects_an_unknown_name():
    with pytest.raises(ValueError, match="manual"):
        UpdatePolicy.from_value("manual")


def test_policy_is_ordered_from_least_to_most_restrictive():
    assert UpdatePolicy.ALLOWED < UpdatePolicy.DISABLED


def test_policy_allows_updates_without_policy_file(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger=LOGGER_ROOT_NAME):
        assert UpdatePolicy.load(tmp_path) is UpdatePolicy.ALLOWED

    assert caplog.records == []


def test_policy_allows_updates_when_the_file_has_no_update_policy(tmp_path, caplog):
    write_policy(tmp_path, json.dumps({"other_setting": True}))

    with caplog.at_level(logging.INFO, logger=LOGGER_ROOT_NAME):
        assert UpdatePolicy.load(tmp_path) is UpdatePolicy.ALLOWED

    assert warnings_logged(caplog) == []


@pytest.mark.parametrize(
    "value, expected",
    [
        ("allowed", UpdatePolicy.ALLOWED),
        ("disabled", UpdatePolicy.DISABLED),
        ("DISABLED", UpdatePolicy.DISABLED),
    ],
)
def test_policy_reads_update_policy_from_the_file(tmp_path, value, expected):
    write_policy(tmp_path, json.dumps({"update_policy": value}))

    assert UpdatePolicy.load(tmp_path) is expected


def test_policy_logs_the_applied_policy_and_its_file(tmp_path, caplog):
    policy_file = write_policy(tmp_path, '{"update_policy": "disabled"}')

    with caplog.at_level(logging.INFO, logger=LOGGER_ROOT_NAME):
        UpdatePolicy.load(tmp_path)

    assert any("disabled" in r.message and str(policy_file) in r.message for r in caplog.records)


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp1252"])
def test_policy_reads_files_in_the_usual_encodings(tmp_path, encoding):
    write_policy(tmp_path, '{"comment": "géré par le service informatique", "update_policy": "disabled"}', encoding)

    assert UpdatePolicy.load(tmp_path) is UpdatePolicy.DISABLED


@pytest.mark.parametrize(
    "content",
    [
        pytest.param('{"update_policy": "allowed"', id="invalid json"),
        pytest.param("", id="empty file"),
        pytest.param('["allowed"]', id="list root"),
        pytest.param('"allowed"', id="string root"),
        pytest.param('{"update_policy": "manual"}', id="unknown value"),
        pytest.param('{"update_policy": ""}', id="empty value"),
        pytest.param('{"update_policy": true}', id="boolean value"),
        pytest.param('{"update_policy": 1}', id="number value"),
        pytest.param('{"update_policy": null}', id="null value"),
    ],
)
def test_policy_disables_updates_when_the_file_is_invalid(tmp_path, caplog, content):
    """An invalid policy must never let updates through, nor stop the client from starting"""
    write_policy(tmp_path, content)

    with caplog.at_level(logging.WARNING, logger=LOGGER_ROOT_NAME):
        assert UpdatePolicy.load(tmp_path) is UpdatePolicy.DISABLED

    assert warnings_logged(caplog)


def test_policy_disables_updates_when_the_file_can_not_be_read(tmp_path, monkeypatch, caplog):
    write_policy(tmp_path, '{"update_policy": "allowed"}')

    def denied_open(*args, **kwargs):
        raise PermissionError("access denied")

    monkeypatch.setattr("builtins.open", denied_open)

    with caplog.at_level(logging.WARNING, logger=LOGGER_ROOT_NAME):
        assert UpdatePolicy.load(tmp_path) is UpdatePolicy.DISABLED

    assert any("access denied" in message for message in warnings_logged(caplog))


def test_policy_is_read_next_to_the_executable_by_default(tmp_path, monkeypatch):
    """By default in the install folder, which is the executable's folder only once frozen"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Pytre_Client.exe"))
    write_policy(tmp_path, '{"update_policy": "disabled"}')

    assert UpdatePolicy.load() is UpdatePolicy.DISABLED


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


def test_build_workflow_builds_the_client_folder_with_the_spec_option():
    """The workflow passes --onedir to the client spec, which must declare it - an unknown option fails the build"""
    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    spec = (ROOT / "Pytre_Client.spec").read_text(encoding="utf-8")

    assert "-- --onedir" in workflow
    assert 'add_argument("--onedir"' in spec


def test_client_folder_build_names_its_contents_folder_after_the_version():
    """Each version has its own folder: a new one can be extracted next to the running one"""
    spec = (ROOT / "Pytre_Client.spec").read_text(encoding="utf-8")

    assert f'contents_directory = f"{CONTENTS_PREFIX}{{full_version()}}"' in spec
    assert "contents_directory=contents_directory" in spec


def test_update_extracts_the_contents_folder_named_by_the_build():
    """The spec names the folder from about.py, the update from the release tag: both must give the same name"""
    tag_version = AppVersion.build_from_string(expected_git_tag())

    assert f"{CONTENTS_PREFIX}{tag_version}" == f"{CONTENTS_PREFIX}{full_version()}"


def test_build_workflow_zips_the_client_folder_under_its_basename():
    """extract_onedir() expects a `<basename>/` root folder in the zip"""
    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")

    assert "base_dir=name" in workflow
    assert "NAME: ${{ matrix.name }}" in workflow


def test_policy_template_installed_by_the_msi_disables_updates():
    template_folder = ROOT / "templates"

    assert (template_folder / POLICY_FILE).is_file()
    assert UpdatePolicy.load(template_folder) is UpdatePolicy.DISABLED


def test_release_publishes_and_checksums_the_client_zip():
    """The zip is verified like the executables: published with the release and listed in SHA256SUMS.txt"""
    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
    checksums_line = next(line for line in workflow.splitlines() if "> SHA256SUMS.txt" in line)

    assert "dist/*.zip" in workflow
    assert "*.zip" in checksums_line
