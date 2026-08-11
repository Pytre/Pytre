from pathlib import Path

from Pytre_Core.utils import get_app_path


APP_NAME = "Pytre"
APP_VERSION = "1.8.1"
APP_STATUS = ""
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
