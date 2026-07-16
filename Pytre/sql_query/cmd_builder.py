import re
from datetime import datetime
from pathlib import Path

import settings
from user_prefs import UserPrefs, PrefsEnum
from sql_query.parameters import SqlParam, SqlParameters
from sql_query.execute_config import ExecuteConfig

REPLACE_PARAM_PATTERN = re.compile(r"(?<![\d\w#_\$@])(@[\d\w#_\$@]+)")  # for cmd_builder


class CmdBuilder:
    def __init__(self, settings: settings.Settings, user_prefs: UserPrefs, raw_cmd: str, parameters: SqlParameters):
        self.settings: settings.Settings = settings
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

    def build_exec_config(self, query_name: str, server_id: str, file_output: bool = True) -> ExecuteConfig:
        # template et paramètres de la commande
        cmd_template: str
        cmd_parameters: dict = {}
        cmd_template, cmd_parameters = self.build_command()

        # fichier d'extraction à utiliser
        extract_file: Path = Path()
        if file_output:
            file_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            extract_file = self.user_prefs.get(PrefsEnum.cache_folder) / (f"{query_name}_{file_stamp}.csv")

        # metadata des paramètres
        params_metadata: dict = {}
        param: SqlParam
        for key, param in self.parameters.params.items():
            params_metadata[key] = {
                "description": param.description,
                "val_cmd": param.value_cmd,
                "val_display": param.display_value,
            }

        # renvoi de l'objet config
        return ExecuteConfig(
            query_name=query_name,
            server_id=server_id,
            cmd_template=cmd_template,
            cmd_parameters=cmd_parameters,
            extract_file=extract_file,
            extract_encoding=self.settings.extract_encoding,
            params_metadata=params_metadata,
        )

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
