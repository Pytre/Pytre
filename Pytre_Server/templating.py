"""One Jinja2 environment shared by every route module, so what every page needs
(such as the app version shown in the sidebar) is registered once."""

from pathlib import Path
from typing import Any, cast

from fastapi.templating import Jinja2Templates

from Pytre_Core.about import full_version

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

app_version = full_version()
cast(dict[str, Any], templates.env.globals)["app_version"] = app_version
