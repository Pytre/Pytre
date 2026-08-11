from pathlib import Path

from Pytre.context import AppContext, UserContext
from Pytre.servers import ServerConnectionError
from Pytre.sql_query.file_parser import FileParser
from Pytre.sql_query.parameters import SqlParam, SqlParameters
from Pytre.sql_query.cmd_builder import CmdBuilder
from Pytre.sql_query.query_execute import QueryExecute
from Pytre.sql_query.execute_config import ExecuteConfig
from Pytre.sql_query.execution_msg import ExecResultMsg
from Pytre.sql_query.log_utils import _STANDALONE_BROADCAST_LOGGER


class Query:
    def __init__(self, app_ctx: AppContext, user_ctx: UserContext, filename: Path = Path(), encoding: str = "utf-8"):
        parser = FileParser(filename, encoding)

        self.app_ctx: AppContext = app_ctx
        self.user_ctx: UserContext = user_ctx
        self.filename: Path = filename
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

        self._query_execute: QueryExecute | None = None

    def _set_hide_level(self) -> int:
        hide: str = self.infos.get("hide", "0")
        return int(hide) if hide.isdigit() else 0

    def _set_description(self) -> str:
        description: str = self.infos.get("description", "")
        if self.hide:
            description = f"({'*' * self.hide}) {description}"

        return description

    @property
    def query_execute(self) -> QueryExecute:
        if self._query_execute is None:
            self._query_execute = QueryExecute(self.app_ctx, self.user_ctx)
        return self._query_execute

    # ------------------------------------------------------------------------------------------
    # Gestion des paramètres avec SqlParameters
    # ------------------------------------------------------------------------------------------
    def reset_values(self):
        self.parameters.reset_values()

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

    def get_exec_config(self, server_id: str = "", file_output: bool = True) -> ExecuteConfig:
        select_server_id: str = server_id or (self.servers_id[0] if self.servers_id else "")
        return self.cmd_builder.build_exec_config(self.name, select_server_id, file_output)

    # ------------------------------------------------------------------------------------------
    # Gestion execution requête et autres fonctions
    # ------------------------------------------------------------------------------------------
    def execute(self, server_id: str = "", file_output: bool = True) -> tuple[int, Path | list[list[str]] | None]:
        self.update_values()

        if self.values_ok():
            exec_config = self.get_exec_config(server_id, file_output)
            try:
                result = self.query_execute.execute(exec_config)
                return result
            except ServerConnectionError as err:
                self._execute_operational_errors(err)
                return (-1, None)
            except Exception as err:
                self._broadcast(f"Erreur inattendue : {err}")
                return (-1, None)
        else:
            err_msg = "\nImpossible d'executer des paramètres sont invalides"
            self._broadcast(err_msg)
            return (-1, None)

    def _execute_operational_errors(self, err: ServerConnectionError):
        err_msg = str("=") * 50 + "\n" + str(err)
        if not err_msg[-1] == "\n":
            err_msg += "\n"
        err_msg += str("=") * 50
        self._broadcast(err_msg)

    def _broadcast(self, msg_to_broadcast: str, msg_type: ExecResultMsg = ExecResultMsg.UI_OUTPUT) -> None:
        self.query_execute.queue.put((msg_type, msg_to_broadcast))
        _STANDALONE_BROADCAST_LOGGER.info(msg_to_broadcast)

    def serialize(self) -> dict:
        display_params: dict[str, str] = {}
        param: SqlParam
        for key, param in self.parameters.params.items():
            display_params[key] = param.display_value

        return {
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
    def deserialize(cls, app_ctx: AppContext, user_ctx: UserContext, data: dict) -> "Query":
        query: Query = cls(app_ctx, user_ctx)

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
        for key, val in display_params.items():
            query.parameters.params[key].display_value = val

        query.update_values()

        return query
