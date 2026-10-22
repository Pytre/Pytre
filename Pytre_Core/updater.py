import hashlib
import logging
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpx2

from Pytre_Core.about import REPO, AppStage, AppVersion
from Pytre_Core.credentials import secret_key_available
from Pytre_Core.utils import log as _log


CHECKSUMS_ASSET: str = "SHA256SUMS.txt"  # published with every release by the build workflow
NEW_SUFFIX: str = ".new"  # downloaded executable, next to the running one until swapped
OLD_SUFFIX: str = ".old"  # previous executable, deleted on next start

_API_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
_REQUEST_TIMEOUT = 10  # seconds - releases list and checksums file
_DOWNLOAD_TIMEOUT = httpx2.Timeout(10, read=60)  # seconds between two chunks


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


class UpdateError(Exception):
    """Raised when the releases can't be retrieved or an update can't be downloaded, verified or applied"""


@dataclass(frozen=True)
class Release:
    version: AppVersion
    page_url: str  # release page on GitHub, for a manual download
    exe_name: str
    exe_url: str
    exe_size: int
    checksums_url: str

    def retrieve_expected_hash(self) -> str:
        """Downloads checkshums from `checksums_url` and return hash for `exe_name`"""
        response = httpx2.get(self.checksums_url, follow_redirects=True, timeout=_REQUEST_TIMEOUT)
        response.raise_for_status()

        for line in response.text.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip("*") == self.exe_name:
                return parts[0].lower()

        raise UpdateError(f"{self.exe_name} missing from checksums asset: {self.checksums_url}")

    def download_exe(self, target_exe: Path, on_progress: Callable[[int, int], None] | None = None) -> Path:
        """Downloads the executable next to `target_exe` and checks its size and SHA-256.
        `on_progress(received_bytes, total_bytes)` is called after each chunk.
        Returns the downloaded file. Raises UpdateError, leaving no partial file behind."""
        new_file = target_exe.with_name(target_exe.name + NEW_SUFFIX)
        digest = hashlib.sha256()
        received = 0

        try:
            expected_hash = self.retrieve_expected_hash()

            with httpx2.stream("GET", self.exe_url, follow_redirects=True, timeout=_DOWNLOAD_TIMEOUT) as response:
                response.raise_for_status()
                with open(new_file, "wb") as f:
                    for chunk in response.iter_bytes():
                        f.write(chunk)
                        digest.update(chunk)
                        received += len(chunk)
                        if on_progress:
                            on_progress(received, self.exe_size)

            if received != self.exe_size:
                raise UpdateError(f"Unexpected file bytes size: {received} received, {self.exe_size} expected")
            if digest.hexdigest() != expected_hash:
                raise UpdateError("Hash from downloaded file does not match expected hash")
        except (httpx2.HTTPError, OSError, UpdateError) as e:
            new_file.unlink(missing_ok=True)
            if isinstance(e, UpdateError):
                log(f"{e}", logging.DEBUG)
                raise
            msg = f"Failed to download app update: {e}"
            log(msg, logging.DEBUG)
            raise UpdateError(msg) from e

        log(f"Update {self.version} downloaded and verified: {new_file}")
        return new_file


class Updater:
    def __init__(self, basename: str, version: AppVersion, channel: AppStage = AppStage.RELEASE, repo: str = REPO):
        """`basename`: name of the executable in the releases, e.g. CLIENT_BASENAME.
        `repo` is the repo "owner/name"."""
        self.basename: str = basename
        self.version: AppVersion = version
        self.channel: AppStage = channel
        self.repo: str = repo

    @staticmethod
    def is_updatable() -> bool:
        """True for a frozen executable and without credentials_secrets.py."""
        return bool(getattr(sys, "frozen", False)) and not secret_key_available()

    @staticmethod
    def current_exe() -> Path:
        return Path(sys.executable)

    # ------------------------------------------------------------------------------------------
    # Finding an update
    # ------------------------------------------------------------------------------------------
    def find_update(self, skipped: AppVersion | None = None) -> Release | None:
        """Newest release newer than the running version, allowed by the channel and newer than `skipped`.
        Raises UpdateError if GitHub can't be reached."""
        data = self._fetch_releases()
        candidates = self._candidates(data, skipped=skipped)
        return max(candidates, key=lambda release: cast(Release, release).version, default=None)

    def _fetch_releases(self) -> list[dict[str, Any]]:
        """Latest releases of `repo`, newest first. Raises UpdateError if GitHub can't be reached."""
        url = f"https://api.github.com/repos/{self.repo}/releases"
        try:
            response = httpx2.get(url, params={"per_page": 30}, headers=_API_HEADERS, timeout=_REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except (httpx2.HTTPError, ValueError) as e:
            raise UpdateError(f"Couldn't retrieve available versions: {e}") from e

    def _candidates(self, data: list[dict[str, Any]], skipped: AppVersion | None = None) -> list[Release]:
        """Releases newer than the running version, at least as stable as the channel allows
        and newer than `skipped`"""
        candidates: list[Release] = []
        min_stage = min(self.channel, self.version.stage)

        for release_dict in data:
            if release_dict.get("draft"):
                continue

            release = self._build_release_info(release_dict)
            if release is None:
                continue
            if release.version <= self.version or release.version.stage < min_stage:
                continue
            if skipped and release.version <= skipped:
                continue

            candidates.append(release)

        return candidates

    def _build_release_info(self, data: dict[str, Any]) -> Release | None:
        """None for a tag with unknown format, or a release without an executable and checksums"""
        version_str = data.get("tag_name", "")
        assets_list = data.get("assets", [])
        page_url = data.get("html_url", "")

        try:
            version = AppVersion.build_from_string(version_str)
        except ValueError:
            return None

        assets = {asset.get("name"): asset for asset in assets_list}
        exe_name = f"{self.basename}_{version}.exe"
        exe_asset = assets.get(exe_name)
        checksums_asset = assets.get(CHECKSUMS_ASSET)

        if exe_asset is None or checksums_asset is None:
            return None

        return Release(
            version=version,
            page_url=page_url,
            exe_name=exe_name,
            exe_url=exe_asset["browser_download_url"],
            exe_size=exe_asset["size"],
            checksums_url=checksums_asset["browser_download_url"],
        )

    # ------------------------------------------------------------------------------------------
    # Installing - download while the app runs, install only once its UI loop has ended
    # ------------------------------------------------------------------------------------------
    @staticmethod
    def install(current_exe: Path, new_exe: Path) -> None:
        """Puts `new_exe` in place of `current_exe`, keeping its name. The running executable can't be
        overwritten but can be renamed: `current_exe` -> `<name>.old`, then `new_exe` -> `current_exe`.
        Rolls back if the second rename fails. Raises UpdateError.
        Call it once the UI loop has ended: afterwards sys.executable points to the new version, which
        would then be started by any new execution process."""
        old_file = current_exe.with_name(current_exe.name + OLD_SUFFIX)

        try:
            old_file.unlink(missing_ok=True)  # leftover of a previous update
            current_exe.rename(old_file)
        except OSError as e:
            raise UpdateError(f"Failed to rename {current_exe.name}: {e}") from e

        try:
            new_exe.rename(current_exe)
        except OSError as install_err:
            try:
                old_file.rename(current_exe)
            except OSError as rollback_err:
                msg = f"Couldn't install new version ({install_err}) and rollback failed: {rollback_err}"
                log(msg, logging.ERROR)
                raise UpdateError(msg) from rollback_err
            raise UpdateError(f"Couldn't install new version, rolled back to current: {install_err}") from install_err

        log(f"Executable replaced: {new_exe.name} -> {current_exe.name}")

    @staticmethod
    def restart(exe_path: Path) -> None:
        """Starts `exe_path` as an independent new instance - the reset tells the PyInstaller bootloader not to
        reuse the unpacked files of the current process"""
        env = os.environ.copy()
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        subprocess.Popen([str(exe_path)], env=env, close_fds=True)

    @staticmethod
    def cleanup_previous(exe_path: Path) -> None:
        """Deletes the executable left by a previous update - retried on next start if still locked"""
        old_file = exe_path.with_name(exe_path.name + OLD_SUFFIX)
        try:
            old_file.unlink(missing_ok=True)
        except OSError as e:
            log(f"Couldn't delete {old_file} yet: {e}", logging.DEBUG)

    @staticmethod
    def can_replace_exe(exe_path: Path) -> bool:
        """True if a file can be written next to `exe_path` - os.access() isn't reliable for Windows ACLs"""
        try:
            with tempfile.TemporaryFile(dir=exe_path.parent):
                return True
        except OSError:
            return False
