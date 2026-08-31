from pathlib import Path
from dataclasses import dataclass, field

from Pytre_Core.remotes import RemoteServer


@dataclass(frozen=True)
class LocalExecuteCfg:
    server_id: str = ""
    query_id: str = ""
    cmd_template: str = ""
    extract_file: Path = field(default_factory=Path)
    extract_encoding: str = "utf-8-sig"
    params_metadata: dict = field(default_factory=dict)
    cmd_parameters: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RemoteExecuteCfg(LocalExecuteCfg):
    remote_server: RemoteServer | None = None
    remote_query_id: str = ""  # collision-proof id sent to /execute - query_id
    template_hash: str = ""
    display_parameters: dict[str, str] = field(default_factory=dict)
