"""One Jinja2 environment shared by every route module, so what every page needs
(such as the app version shown in the sidebar) is registered once."""

from pathlib import Path
from typing import Any, cast

from fastapi.templating import Jinja2Templates

from Pytre_Core.about import APP_STATUS, APP_VERSION

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

app_version = APP_VERSION if not APP_STATUS else f"{APP_VERSION} {APP_STATUS}"
cast(dict[str, Any], templates.env.globals)["app_version"] = app_version
