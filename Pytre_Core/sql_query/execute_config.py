from pathlib import Path
from dataclasses import dataclass, field

from Pytre_Core.remotes import RemoteServer
from Pytre_Core.user_prefs import CsvFormat, DisplayFormat


@dataclass(frozen=True)
class LocalExecuteCfg:
    server_id: str = ""
    query_id: str = ""
    cmd_template: str = ""
    extract_file: Path = field(default_factory=Path)
    csv_format: CsvFormat = field(default_factory=CsvFormat)
    params_metadata: dict = field(default_factory=dict)  # used for logs
    cmd_parameters: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RemoteExecuteCfg(LocalExecuteCfg):
    remote_server: RemoteServer | None = None
    remote_query_id: str = ""  # collision-proof id sent to /execute - query_id
    template_hash: str = ""
    display_format: DisplayFormat = field(default_factory=DisplayFormat)  # format display_parameters use
    display_parameters: dict[str, str] = field(default_factory=dict)
