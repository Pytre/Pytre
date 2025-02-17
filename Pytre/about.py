from pathlib import Path

from settings import get_app_path

APP_NAME = "Pytre"
APP_VERSION = "1.3.2"
APP_BUILD = "1"
COPYRIGHT_YEAR = "2021"
AUTHOR = "Matthieu Ferrier"
HOMEPAGE_LINK = "https://github.com/Pytre/Pytre"
LICENSE_NAME = "GNU Affero General Public License"
LICENSE_TEXT = Path(get_app_path() / "res" / "about_license.txt").read_text()
