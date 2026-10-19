from pathlib import Path

from Pytre_Core.utils import get_app_path


APP_NAME = "Pytre"
APP_VERSION = "2.0.0"
APP_STATUS: str | None = "BETA 2"  # github builds will be flagged as pre-release when set
APP_BUILD = "1"
COPYRIGHT_YEAR = "2021"
AUTHOR = "Matthieu Ferrier"
HOMEPAGE_LINK = "https://github.com/Pytre/Pytre"
LICENSE_NAME = "GNU Affero General Public License"


def get_license_text() -> str:
    try:
        return Path(get_app_path() / "res" / "about_license.txt").read_text()
    except OSError as e:
        return f"Error, license unavailable : {e}"


def full_version() -> str:
    """Version with its status if any. No spaces allowed. e.g. '2.0.0_BETA.1'"""
    return f"{APP_VERSION}_{APP_STATUS.replace(' ', '.')}" if APP_STATUS else APP_VERSION


def expected_git_tag() -> str:
    """Git tag expected for the current version, checked by the GitHub build workflow"""
    return f"v.{full_version()}"
