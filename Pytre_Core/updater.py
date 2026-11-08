import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any

import httpx2

from Pytre_Core.about import REPO, AppStage, AppVersion
from Pytre_Core.app_folders import get_install_folder
from Pytre_Core.credentials import secret_key_available
from Pytre_Core.utils import detect_encoding, log as _log


POLICY_FILE: str = "Pytre.policy.json"  # enterprise policy, next to the executable

CHECKSUMS_ASSET: str = "SHA256SUMS.txt"  # published with every release by the build workflow
NEW_SUFFIX: str = ".new"  # downloaded executable, next to the running one until swapped
OLD_SUFFIX: str = ".old"  # previous executable, deleted on next start
DOWNLOAD_SUFFIX: str = ".part"  # downloaded zip of the onedir build, deleted once extracted
CONTENTS_PREFIX: str = "_internal_"  # contents folder of the onedir build, followed by its full version

_API_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
_REQUEST_TIMEOUT = 10  # seconds - releases list and checksums file
_DOWNLOAD_TIMEOUT = httpx2.Timeout(10, read=60)  # seconds between two chunks


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


class UpdateError(Exception):
    """Raised when the releases can't be retrieved or an update can't be downloaded, verified or applied"""


class UpdatePolicy(IntEnum):
    """Updates allowed by the enterprise policy file, from least to most restrictive"""

    ALLOWED = 1  # the user pref decides on the startup check, manual check available
    DISABLED = 2  # no check at all, no network call

    @classmethod
    def from_value(cls, value: str) -> UpdatePolicy:
        """Policy from its name (case insensitive). Raises ValueError otherwise."""
        try:
            return cls[str(value).upper()]
        except KeyError:
            raise ValueError(f"Unknown UpdatePolicy: {value}") from None

    @staticmethod
    def load(folder: Path | None = None) -> UpdatePolicy:
        """Update policy set by `POLICY_FILE` in `folder` (the install folder by default).
        ALLOWED without the file or without `update_policy`, DISABLED when the file can't be read or holds an
        unknown policy. Never raises."""

        folder = folder or get_install_folder()
        file_src = folder / POLICY_FILE

        if not file_src.exists():
            return UpdatePolicy.ALLOWED

        try:
            encoding = detect_encoding(file_src)
            if not encoding:
                raise ValueError("unknown encoding")

            with open(file_src, mode="r", encoding=encoding) as f:
                json_dict = json.load(f)

            if not isinstance(json_dict, dict):
                raise ValueError("root must be a JSON object")
            elif "update_policy" not in json_dict:
                log("No update_policy defined, updates allowed.", logging.INFO)
                policy = UpdatePolicy.ALLOWED
            else:
                policy = UpdatePolicy.from_value(json_dict["update_policy"])
        except (OSError, ValueError) as e:
            log(f"Invalid update policy {file_src.name}, updates disabled: {e}", logging.WARNING)
            policy = UpdatePolicy.DISABLED

        log(f"Update policy: {policy.name.lower()} ({file_src})", logging.INFO)
        return policy


@dataclass(frozen=True)
class Release:
    version: AppVersion
    page_url: str  # release page on GitHub, for a manual download
    basename: str  # name of the executable in the releases, e.g. CLIENT_BASENAME
    asset_name: str  # file to download: the executable, or the zip of the onedir build
    asset_url: str  # url of the file to download
    asset_size: int  # size of the file to download
    checksums_url: str  # url of the checksums file to verify download
    onedir: bool = False  # whether asset is the zip of the onedir build

    def retrieve_expected_hash(self) -> str:
        """Downloads checksums from `checksums_url` and return hash for `asset_name`"""
        response = httpx2.get(self.checksums_url, follow_redirects=True, timeout=_REQUEST_TIMEOUT)
        response.raise_for_status()

        for line in response.text.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip("*") == self.asset_name:
                return parts[0].lower()

        raise UpdateError(f"{self.asset_name} missing from checksums asset: {self.checksums_url}")

    def download_update(self, current_exe: Path, on_progress: Callable[[int, int], None] | None = None) -> Path:
        """Downloads the release and puts the new version next to `current_exe`. Returns the executable that
        Updater.install() swaps in once the UI loop has ended. Raises UpdateError, leaving no new file behind."""
        if not self.onedir:
            return self.download(current_exe.with_name(current_exe.name + NEW_SUFFIX), on_progress)

        zip_file = self.download(current_exe.with_name(self.asset_name + DOWNLOAD_SUFFIX), on_progress)
        try:
            return Updater.extract_onedir(zip_file, current_exe, self.basename, self.version)
        finally:
            Updater.remove_quietly(zip_file)

    def download(self, new_file: Path, on_progress: Callable[[int, int], None] | None = None) -> Path:
        """Downloads the asset to `new_file` and checks its size and SHA-256.
        `on_progress(received_bytes, total_bytes)` is called after each chunk.
        Returns the downloaded file. Raises UpdateError, leaving no partial file behind."""
        digest = hashlib.sha256()
        received = 0

        try:
            expected_hash = self.retrieve_expected_hash()

            with httpx2.stream("GET", self.asset_url, follow_redirects=True, timeout=_DOWNLOAD_TIMEOUT) as response:
                response.raise_for_status()
                with open(new_file, "wb") as f:
                    for chunk in response.iter_bytes():
                        f.write(chunk)
                        digest.update(chunk)
                        received += len(chunk)
                        if on_progress:
                            on_progress(received, self.asset_size)

            if received != self.asset_size:
                raise UpdateError(f"Unexpected file bytes size: {received} received, {self.asset_size} expected")
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
    def __init__(self, basename: str, channel: AppStage = AppStage.RELEASE, repo: str = REPO):
        """Looks for updates of the running build. `basename`: name of the executable in the releases,
        e.g. CLIENT_BASENAME. `repo` is the repo "owner/name"."""
        self.basename: str = basename
        self.channel: AppStage = channel
        self.repo: str = repo
        self.version: AppVersion = AppVersion.current()

    @staticmethod
    def is_onedir() -> bool:
        """True for a onedir build: its bundled files sit in a folder next to the executable (`_internal_<version>`),
        while a onefile build unpacks them in a temporary folder"""
        bundle = getattr(sys, "_MEIPASS", None)
        return bundle is not None and Path(bundle).is_relative_to(Path(sys.executable).parent)

    @staticmethod
    def is_updatable() -> bool:
        """True for a frozen build without credentials_secrets.py.
        A managed deployment turns updates off with the policy file, as the MSI does."""
        frozen = bool(getattr(sys, "frozen", False))
        return frozen and not secret_key_available()

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
        return max(candidates, key=lambda release: release.version, default=None)

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
        """None for a tag with unknown format, or a release without checksums or the asset of the running build:
        the executable, or the zip for a onedir build"""
        version_str = data.get("tag_name", "")
        assets_list = data.get("assets", [])
        page_url = data.get("html_url", "")

        try:
            version = AppVersion.build_from_string(version_str)
        except ValueError:
            return None

        assets = {asset.get("name"): asset for asset in assets_list}

        onedir = Updater.is_onedir()  # a folder build is updated from the release zip
        asset_name = f"{self.basename}_{version}.{'zip' if onedir else 'exe'}"
        asset = assets.get(asset_name)
        checksums_asset = assets.get(CHECKSUMS_ASSET)

        if asset is None or checksums_asset is None:
            return None

        return Release(
            version=version,
            page_url=page_url,
            basename=self.basename,
            asset_name=asset_name,
            asset_url=asset["browser_download_url"],
            asset_size=asset["size"],
            checksums_url=checksums_asset["browser_download_url"],
            onedir=onedir,
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
    def extract_onedir(zip_file: Path, current_exe: Path, basename: str, version: AppVersion) -> Path:
        """Extracts the onedir build of `version` next to `current_exe`: its contents folder `_internal_<version>`,
        then its executable as `<name>.new`, the one install() swaps in. The zip holds a `<basename>/` root folder
        (see the build workflow): any other entry, or one leaving its target folder, makes it fail. On failure the
        contents folder and the executable are both removed. Returns the new executable. Raises UpdateError."""
        contents_folder = current_exe.parent / f"{CONTENTS_PREFIX}{version}"
        new_exe = current_exe.with_name(current_exe.name + NEW_SUFFIX)
        root_entry = f"{basename}/"
        exe_entry = f"{basename}/{basename}.exe"
        contents_entry = f"{basename}/{contents_folder.name}/"

        try:
            Updater.remove_quietly(contents_folder, new_exe)  # leftovers of a failed attempt
            if contents_folder.exists() or new_exe.exists():
                raise UpdateError(f"Couldn't delete the leftovers of a previous attempt in {current_exe.parent}")

            with zipfile.ZipFile(zip_file) as archive:
                zip_entries = [entry for entry in archive.infolist() if entry.filename != root_entry]
                zip_exe_entry = next((entry for entry in zip_entries if entry.filename == exe_entry), None)
                if zip_exe_entry is None:
                    raise UpdateError(f"{exe_entry} missing from {zip_file.name}")
                zip_entries.remove(zip_exe_entry)

                for zip_entry in zip_entries:
                    if not zip_entry.filename.startswith(contents_entry):
                        raise UpdateError(f"Unexpected entry in {zip_file.name}: {zip_entry.filename}")

                    target = contents_folder / zip_entry.filename.removeprefix(contents_entry)
                    if not target.resolve().is_relative_to(contents_folder.resolve()):
                        raise UpdateError(f"Entry leaving its folder in {zip_file.name}: {zip_entry.filename}")

                    if zip_entry.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(zip_entry) as source, open(target, "wb") as dest:
                            shutil.copyfileobj(source, dest)

                # the executable last: install() only finds a .new once its contents folder is complete
                with archive.open(zip_exe_entry) as source, open(new_exe, "wb") as dest:
                    shutil.copyfileobj(source, dest)
        except (OSError, EOFError, zipfile.BadZipFile, UpdateError) as e:
            Updater.remove_quietly(contents_folder, new_exe)
            if isinstance(e, UpdateError):
                log(f"{e}", logging.DEBUG)
                raise
            msg = f"Failed to extract app update: {e}"
            log(msg, logging.DEBUG)
            raise UpdateError(msg) from e

        log(f"Update {version} extracted: {contents_folder.name}, {new_exe.name}")
        return new_exe

    @staticmethod
    def restart(exe_path: Path) -> None:
        """Starts `exe_path` as an independent new instance - the reset tells the PyInstaller bootloader not to
        reuse the unpacked files of the current process"""
        env = os.environ.copy()
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        subprocess.Popen([str(exe_path)], env=env, close_fds=True)

    @staticmethod
    def cleanup_previous(exe_path: Path) -> bool:
        """Deletes the executable left by a previous update - retried on next start if still locked.
        True once there is none left: no window of the previous version is still running."""
        old_file = exe_path.with_name(exe_path.name + OLD_SUFFIX)
        try:
            old_file.unlink(missing_ok=True)
            return True
        except OSError as e:
            log(f"Couldn't delete {old_file} yet: {e}", logging.DEBUG)
            return False

    @staticmethod
    def cleanup_contents_folders(exe_path: Path, version: AppVersion) -> None:
        """Deletes the contents folders of the versions older than `version` next to `exe_path`. Call it only once
        cleanup_previous() succeeded: a window of an older version could still be running otherwise. Never touches
        the running version, a newer one (another window may be extracting it) or a name without a version."""
        for folder in exe_path.parent.glob(f"{CONTENTS_PREFIX}*"):
            try:
                folder_version = AppVersion.build_from_string(folder.name.removeprefix(CONTENTS_PREFIX))
            except ValueError:
                continue
            if folder.is_dir() and folder_version < version:
                Updater.remove_quietly(folder)

    @staticmethod
    def remove_quietly(*paths: Path) -> None:
        """Deletes files and folders that may not exist, logging those that can't be deleted"""
        for path in paths:
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            except OSError as e:
                log(f"Couldn't delete {path}: {e}", logging.DEBUG)

    @staticmethod
    def can_replace_exe(exe_path: Path) -> bool:
        """True if a file can be written next to `exe_path` - os.access() isn't reliable for Windows ACLs"""
        try:
            with tempfile.TemporaryFile(dir=exe_path.parent):
                return True
        except OSError:
            return False
