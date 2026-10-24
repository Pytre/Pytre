import re
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import ClassVar

from Pytre_Core.utils import get_app_path


class AppStage(IntEnum):
    """Release stage, from least to most stable"""

    ALPHA = 1
    BETA = 2
    RC = 3
    RELEASE = 4

    @classmethod
    def from_value(cls, value: AppStage | int | str) -> AppStage:
        """Accepts an AppStage, its number or its name (case insensitive). Raises ValueError otherwise."""
        if isinstance(value, AppStage):
            return value
        if isinstance(value, int):
            return cls(value)  # raises ValueError on an unknown number
        try:
            return cls[str(value).upper()]
        except KeyError:
            raise ValueError(f"Unknown AppStage: {value}") from None


APP_NAME: str = "Pytre"
COPYRIGHT_YEAR: str = "2021"
AUTHOR: str = "Matthieu Ferrier"
LICENSE_NAME: str = "GNU Affero General Public License"
REPO: str = "Pytre/Pytre"
HOMEPAGE_LINK: str = f"https://github.com/{REPO}"

APP_VERSION: tuple[int, int, int] = (2, 0, 0)  # major, minor, patch numbers
APP_STAGE: AppStage = AppStage.BETA  # build flagged as pre-release when different than RELEASE
APP_STAGE_NUMBER: int = 3  # 0 when the stage has no number or for RELEASE

# release asset names: <basename>_<version>.exe
CLIENT_BASENAME: str = "Pytre_Client"
SERVER_BASENAME: str = "Pytre_Server"


@dataclass(frozen=True, order=True)
class AppVersion:
    """Version split into comparable parts. Ordered by version numbers, then stage, then stage number."""

    version: tuple[int, int, int]
    stage: AppStage = AppStage.RELEASE
    stage_number: int = 0

    # "2.0.0", "2.0.0-alpha", "2.0.0-beta.2" and git tags "v.2.0.0-beta.2"
    _PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"^(?:v\.)?"
        r"(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
        r"(?:-(?P<stage>[A-Za-z]+)(?:\.(?P<stage_number>\d+))?)?$"
    )

    def __str__(self) -> str:
        if self.stage is AppStage.RELEASE:
            return self.base_version
        return f"{self.base_version}-{self.stage.name.lower()}" + (
            f".{self.stage_number}" if self.stage_number else ""
        )

    @property
    def base_version(self) -> str:
        """Version numbers only, e.g. '2.0.0'"""
        return ".".join(str(d) for d in self.version)

    @property
    def binary_version(self) -> tuple[int, int, int, int]:
        return *self.version, self.stage * 100 + self.stage_number

    @property
    def is_prerelease(self) -> bool:
        return self.stage is not AppStage.RELEASE

    @classmethod
    def build_from_string(cls, text: str) -> AppVersion:
        """Parses app version or a git tag (starting with 'v.'). Raises ValueError on unknown format or stage."""
        match = cls._PATTERN.match(text.strip())
        if not match:
            raise ValueError(f"Invalid version format: {text!r}")

        stage = AppStage.from_value(match["stage"] or AppStage.RELEASE)

        return cls(
            version=(int(match["major"]), int(match["minor"]), int(match["patch"])),
            stage=stage,
            stage_number=int(match["stage_number"]) if match["stage_number"] else 0,
        )

    @classmethod
    def current(cls) -> AppVersion:
        return cls(version=APP_VERSION, stage=APP_STAGE, stage_number=APP_STAGE_NUMBER)


def get_license_text() -> str:
    try:
        return Path(get_app_path() / "res" / "about_license.txt").read_text()
    except OSError as e:
        return f"Error, license unavailable : {e}"


def full_version() -> str:
    """Version with its status if any. No spaces allowed. e.g. '2.0.0-beta.1'"""
    return str(AppVersion.current())


def expected_git_tag() -> str:
    """Git tag expected for the current version, checked by the GitHub build workflow"""
    return f"v.{full_version()}"
