from pathlib import Path
from typing import Self

from Pytre_Core.context import AppContext, UserContext
from Pytre_Core.remotes import RemoteServer
from Pytre_Core.sql_query.file_parser import FileParser
from Pytre_Core.sql_query.parameters import SqlParam, SqlParameters
from Pytre_Core.sql_query.cmd_builder import CmdBuilder
from Pytre_Core.sql_query.execute_config import LocalExecuteCfg, RemoteExecuteCfg


class Query:
    def __init__(self, app_ctx: AppContext, user_ctx: UserContext, source: Path | FileParser = Path()):
        """source can be a Path (it will be read and parsed)
        or an already-built FileParser (reused as-is without disk read)"""
        parser = source if isinstance(source, FileParser) else FileParser(source)
        self.remote: RemoteServer | None = None  # initialisé en direct ou avec deserialize

        self.app_ctx: AppContext = app_ctx
        self.user_ctx: UserContext = user_ctx
        self.filename: Path = parser.filename
        self.file_content: str = parser.content
        self.infos: dict = parser.extract_header_infos()
        self.raw_cmd: str = parser.extract_raw_command()

        self.name: str = self.infos.get("code", self.filename.stem)
        self.hide: int = self._set_hide_level()
        self.description: str = self._set_description()
        self.grp_authorized: list[str] = self.infos.get("grp_authorized", [])
        all_servers_id = list(app_ctx.servers.servers_dict.keys())
        self.servers_id: list[str] = self.infos.get("servers", all_servers_id)

        self.parameters = SqlParameters(self.app_ctx.settings, self.user_ctx.current_user, self.file_content)
        self.cmd_builder = CmdBuilder(self.app_ctx.settings, self.user_ctx.user_prefs, self.raw_cmd, self.parameters)

    def _set_hide_level(self) -> int:
        hide: str = self.infos.get("hide", "0")
        return int(hide) if hide.isdigit() else 0

    def _set_description(self) -> str:
        description: str = self.infos.get("description", "")
        if self.hide:
            description = f"({'*' * self.hide}) {description}"

        return description

    # ------------------------------------------------------------------------------------------
    # Gestion des paramètres avec SqlParameters
    # ------------------------------------------------------------------------------------------
    def reset_values(self):
        self.parameters.reset_values()

    def set_display_values(self, values: dict[str, str]) -> None:
        """
        Set display values from a dict with param_id as key and validate them.
        Raise KeyError if key does not exists and ValueError if value is not valid.
        """
        self.parameters.set_display_values(values)

    def update_values(self, key: str | None = None) -> bool:
        """
        If key is none then update all key
        """
        return self.parameters.update_values(key)

    def values_ok(self, key: str | None = None) -> bool:
        """
        If key is none then update all key
        """
        return self.parameters.validate(key)

    # ------------------------------------------------------------------------------------------
    # Construction requête à executer avec SqlCmdBuilder
    # ------------------------------------------------------------------------------------------
    def get_cmd_for_debug(self) -> str:
        return self.cmd_builder.build_debug_command()

    def get_params_for_debug(self, only_inlined: bool = False) -> dict[int, int]:
        """position et longueur des paramètres après remplacement par leurs valeurs"""
        return self.cmd_builder.params_pos_for_debug(only_inlined)

    def get_exec_config(self, server_id: str = "", file_output: bool = True) -> LocalExecuteCfg | RemoteExecuteCfg:
        if self.remote:
            return self.cmd_builder.build_exec_remote_cfg(self.remote, self.name, file_output)
        else:
            select_server_id: str = server_id or (self.servers_id[0] if self.servers_id else "")
            return self.cmd_builder.build_exec_local_cfg(select_server_id, self.name, file_output)

    def get_template_hash(self) -> str:
        return self.cmd_builder.get_template_hash()

    # ------------------------------------------------------------------------------------------
    # Serialization / Deserialization
    # ------------------------------------------------------------------------------------------
    def serialize(self) -> dict:
        display_params: dict[str, str] = {}
        param: SqlParam
        for key, param in self.parameters.params.items():
            display_params[key] = param.display_value

        return {
            "remote": self.remote.serialize() if self.remote else None,
            "filename": str(self.filename),
            "file_content": self.file_content,
            "infos": self.infos,
            "raw_cmd": self.raw_cmd,
            "name": self.name,
            "hide": self.hide,
            "description": self.description,
            "grp_authorized": self.grp_authorized,
            "servers_id": self.servers_id,
            "display_params": display_params,
            "cmd_template": self.cmd_builder.template,
        }

    @classmethod
    def deserialize(
        cls, app_ctx: AppContext, user_ctx: UserContext, data: dict, remote: RemoteServer | None = None
    ) -> Self:
        query: Query = cls(app_ctx, user_ctx)

        query.remote = remote or (RemoteServer.deserialize(data["remote"]) if data["remote"] else None)
        query.filename = Path(data["filename"])
        query.file_content = data["file_content"]
        query.infos = data["infos"]
        query.raw_cmd = data["raw_cmd"]
        query.name = data["name"]
        query.hide = data["hide"]
        query.description = data["description"]
        query.grp_authorized = data["grp_authorized"]
        query.servers_id = data["servers_id"]

        query.parameters = SqlParameters(app_ctx.settings, user_ctx.current_user, query.file_content)
        query.cmd_builder = CmdBuilder(app_ctx.settings, user_ctx.user_prefs, query.raw_cmd, query.parameters)

        display_params: dict[str, str] = data["display_params"]
        query.set_display_values(display_params)

        return query
