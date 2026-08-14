import hashlib
import re
from datetime import datetime
from pathlib import Path

from Pytre_Core.remotes import RemoteServer
from Pytre_Core.settings import Settings
from Pytre_Core.user_prefs import UserPrefs, PrefsEnum
from Pytre_Core.sql_query.parameters import SqlParam, SqlParameters
from Pytre_Core.sql_query.execute_config import LocalExecuteCfg, RemoteExecuteCfg

REPLACE_PARAM_PATTERN = re.compile(r"(?<![\d\w#_\$@])(@[\d\w#_\$@]+)")  # for cmd_builder


class CmdBuilder:
    def __init__(self, settings: Settings, user_prefs: UserPrefs, raw_cmd: str, parameters: SqlParameters):
        self.settings: Settings = settings
        self.user_prefs: UserPrefs = user_prefs
        self.raw_cmd: str = raw_cmd
        self.parameters: SqlParameters = parameters
        self.template: str = self._build_template()

    def _build_template(self) -> str:
        """Crée le template avec %(var)s"""

        def replace_func(match: re.Match):
            # replace only variables which are parameters
            if match.group(0) in self.parameters.params:
                return f"%({match.group(0)})s"
            else:
                return match.group(0)

        cmd_template = REPLACE_PARAM_PATTERN.sub(replace_func, self.raw_cmd)

        return cmd_template

    def build_exec_local_cfg(self, server_id: str, query_id: str, file_output: bool = True) -> LocalExecuteCfg:
        cmd_template, cmd_parameters = self.build_command()

        # fichier d'extraction à utiliser
        extract_file: Path = Path()
        if file_output:
            file_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            extract_file = self.user_prefs.get(PrefsEnum.cache_folder) / (f"{query_id}_{file_stamp}.csv")

        # metadata des paramètres
        params_metadata: dict = {}
        param: SqlParam
        for key, param in self.parameters.params.items():
            params_metadata[key] = {
                "description": param.description,
                "val_cmd": param.value_cmd,
                "val_display": param.display_value,
            }

        return LocalExecuteCfg(
            server_id=server_id,
            query_id=query_id,
            cmd_template=cmd_template,
            extract_file=extract_file,
            extract_encoding=self.settings.extract_encoding,
            params_metadata=params_metadata,
            cmd_parameters=cmd_parameters,
        )

    def build_exec_remote_cfg(self, server: RemoteServer, query_id: str, file_output: bool = True) -> RemoteExecuteCfg:
        base_cfg = self.build_exec_local_cfg(server.id_on_remote, query_id, file_output=file_output)

        display_parameters: dict[str, str] = {}
        param: SqlParam
        for key, param in self.parameters.params.items():
            display_parameters[key] = param.display_value

        return RemoteExecuteCfg(
            server_id=server.id_on_remote,
            query_id=query_id,
            cmd_template=base_cfg.cmd_template,
            extract_file=base_cfg.extract_file,
            extract_encoding=self.settings.extract_encoding,
            params_metadata=base_cfg.params_metadata,
            cmd_parameters=base_cfg.cmd_parameters,
            remote_server=server,
            template_hash=self.get_template_hash(),
            display_parameters=display_parameters,
        )

    def get_template_hash(self) -> str:
        return hashlib.sha256(self.template.encode("utf-8")).hexdigest()

    def build_command(self) -> tuple[str, dict]:
        """Construit la commande finale avec remplacement des @! variables"""
        cmd_template = self.template
        cmd_parameters = {}

        # tri décroissant pour éviter replace à tort (par exemple @!ID vs @!ID2)
        sorted_params = sorted(self.parameters.params.items(), key=lambda item: len(item[0]), reverse=True)

        for key, param in sorted_params:
            if param.is_inlined:
                cmd_template = cmd_template.replace(key, str(param.value_cmd))
            else:
                cmd_parameters[key] = param.value_cmd

        return cmd_template, cmd_parameters

    def build_debug_command(self) -> str:
        """Retourne la commande pour debug"""
        cmd_debug = self.template

        # tri décroissant pour éviter replace à tort (par exemple @!ID vs @!ID2)
        sorted_params = sorted(self.parameters.params.items(), key=lambda item: len(item[0]), reverse=True)

        for key, param in sorted_params:
            if param.is_inlined:  # pas une variable
                cmd_debug = cmd_debug.replace(key, str(param.value_cmd))
                continue
            elif isinstance(param.value_cmd, str):
                value = "'" + param.value_cmd.replace("'", "''") + "'"
            else:
                value = str(param.value_cmd)

            cmd_debug = cmd_debug.replace(f"%({key})s", value)

        return cmd_debug

    def params_pos_for_debug(self, only_inlined: bool = False) -> dict[int, int]:
        """Position et longueur des paramètres après remplacement par leurs valeurs"""
        offset: int = 0
        var_pos: dict[int, int] = {}

        for match in re.finditer(r"(?<![\d\w#_\$@])(@!?[\d\w#_\$@]+)", self.raw_cmd):
            matched_param: str = match.group(0)
            sql_param: SqlParam | None = self.parameters.params.get(matched_param, None)

            if not sql_param:
                continue
            if only_inlined and not sql_param.is_inlined:
                continue

            value_cmd = sql_param.value_cmd
            if isinstance(value_cmd, str) and not sql_param.is_inlined:
                value_cmd = "'" + value_cmd.replace("'", "''") + "'"
            else:
                value_cmd = str(value_cmd)

            start = offset + match.start()
            end = offset + match.end() + len(value_cmd) - len(matched_param)
            var_pos[start] = end - start
            offset += len(value_cmd) - len(matched_param)

        return var_pos
