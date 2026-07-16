from pathlib import Path
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExecuteConfig:
    query_name: str = ""
    server_id: str = ""
    cmd_template: str = ""
    cmd_parameters: dict = field(default_factory=dict)
    extract_file: Path = field(default_factory=Path)
    extract_encoding: str = "utf-8-sig"
    params_metadata: dict = field(default_factory=dict)
