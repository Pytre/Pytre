import re
import csv
import codecs
from collections.abc import Callable
from datetime import datetime
from dateutil.relativedelta import relativedelta
from pathlib import Path
from multiprocessing import Queue, Event as proc_get_event
from multiprocessing.synchronize import Event as ProcEvent
from dataclasses import dataclass, field

import pymssql
import psycopg2

import settings
import users
import servers
import logs_user
from user_prefs import UserPrefs, PrefsEnum
from sql_converter import SqlTypeConverter

PRINT_DATE_FORMAT: str = "%d/%m/%Y à %H:%M:%S"  # pour le format de la date pour les logs / output

REPLACE_PARAM_PATTERN = re.compile(r"(?<![\d\w#_\$@])(@[\d\w#_\$@]+)")  # for cmd_builder
DECLARE_EXTRACT_PATTERN = re.compile(r"^(DECLARE[^;]*;)", re.MULTILINE | re.DOTALL)  # for SqlParameters

# Patterns for parsing SQL parameters when SqlParam is initializated
VAR_NAME_PATTERN = re.compile(r"^@!?[\d\w#_\$@]+")
TYPE_NAME_PATTERN = re.compile(r"^[^(=]+as\s+([^(\s=]+)", re.IGNORECASE)
DISPLAY_VALUE_PATTERN = re.compile(r"^[^=]*=\s*('.*?'|[^,\s]*)")
TYPE_ARGS_PATTERN = re.compile(r"^[^(=]+\(([^)]+)\)")
COMMENT_PATTERN = re.compile(r"--\s*(.+)")
INFO_LIST_PATTERN = re.compile(r"^(.*?)(?=\(|$)\(?(.*?)\)?$")


@dataclass(frozen=True)
class ExecuteConfig:
    query_name: str = ""
    server_id: str = ""
    cmd_template: str = ""
    cmd_parameters: dict = field(default_factory=dict)
    extract_file: Path = field(default_factory=Path)
    extract_encoding: str = "utf-8-sig"
    params_metadata: dict = field(default_factory=dict)


class Query:
    def __init__(self, filename: Path = Path(), encoding: str = "utf-8"):
        parser = FileParser(filename, encoding)

        self.filename: Path = filename
        self.file_content: str = parser.content
        self.infos: dict = parser.extract_header_infos()
        self.raw_cmd: str = parser.extract_raw_command()

        self.name: str = self.infos.get("code", self.filename.stem)
        self.hide: int = self._set_hide_level()
        self.description: str = self._set_description()
        self.grp_authorized: list[str] = self.infos.get("grp_authorized", [])
        all_servers_id = list(servers.Servers().servers_dict.keys())
        self.servers_id: list[str] = self.infos.get("servers", all_servers_id)

        self.parameters = SqlParameters(self.file_content)
        self.cmd_builder = CmdBuilder(self.raw_cmd, self.parameters)
        self.query_execute: QueryExecute = QueryExecute()

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
            except (pymssql._pymssql.OperationalError, psycopg2.OperationalError) as err:
                self._execute_operational_errors(err)
                return (-1, None)
            except Exception as err:
                self._broadcast(f"Erreur inattendue : {err}")
                return (-1, None)
        else:
            err_msg = "\nImpossible d'executer des paramètres sont invalides"
            self._broadcast(err_msg)
            return (-1, None)

    def _execute_operational_errors(self, err: Exception):
        err_msg = str("=") * 50 + "\n"

        if isinstance(err, pymssql._pymssql.OperationalError):
            err_msg += err.args[0][1].decode("utf-8", errors="replace")
        if isinstance(err, psycopg2.OperationalError):
            err_msg += "".join(err.args[0])

        if not err_msg[-1] == "\n":
            err_msg += "\n"
        err_msg += str("=") * 50
        self._broadcast(err_msg)

    def _broadcast(self, msg_to_broadcast: str, msg_type: str = "msg_output") -> None:
        self.query_execute.queue.put((msg_type, msg_to_broadcast))
        print(msg_to_broadcast)

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
    def deserialize(cls, data: dict) -> "Query":
        query: Query = cls()

        query.filename = Path(data["filename"])
        query.file_content = data["file_content"]
        query.infos = data["infos"]
        query.raw_cmd = data["raw_cmd"]
        query.name = data["name"]
        query.hide = data["hide"]
        query.description = data["description"]
        query.grp_authorized = data["grp_authorized"]
        query.servers_id = data["servers_id"]

        query.parameters = SqlParameters(query.file_content)
        query.cmd_builder = CmdBuilder(query.raw_cmd, query.parameters)

        display_params: dict[str, str] = data["display_params"]
        for key, val in display_params.items():
            query.parameters.params[key].display_value = val

        query.update_values()

        return query


class FileParser:
    def __init__(self, filename: Path = Path(), encoding: str = "utf-8"):
        self.filename: Path = filename
        self.content: str = self._read_file(encoding)

    def _read_file(self, encoding: str) -> str:
        file_content = ""

        # if filename has not been set then immediatly return without error
        if self.filename == Path():
            return file_content

        # set alternative encoding to try if failing to decode
        encodings: list = ["utf-8", "cp1252", "ansi"]
        encodings.remove(encoding) if encoding in encodings else None
        encodings.insert(0, encoding)

        # test all possible decoding formats
        decoded: bool = False
        for encoding in encodings:
            try:
                with open(self.filename, mode="r", encoding=encoding) as f:
                    file_content = f.read()
                decoded = True
                break
            except (UnicodeDecodeError, LookupError):  # LookupError if format doesn't exist (eg : ansi on linux)
                pass

        # if all try failed raise an exception
        if not decoded:
            raise TypeError(f"couldn't decode file using {', '.join(encodings)}")

        return file_content

    def extract_header_infos(self) -> dict:
        infos = {}
        regex_match = re.search(
            r"^\/\*[^\n]*?\n(.*?)\n^\*\/", self.content, re.MULTILINE | re.DOTALL
        )  # récupération des infos d'entête de la requete (premier bloc de commentaires)

        if regex_match is not None:
            for line in regex_match.group(1).splitlines():
                regex_infos = re.search(r"^([^:]*?)\s*:\s*(.*$)", line)
                if regex_infos is not None:
                    info_key = regex_infos.group(1).lower()

                    convert_to_list = ["grp_authorized", "servers"]
                    if info_key in convert_to_list:
                        info_value = [item.lower().strip() for item in regex_infos.group(2).split(",")]
                    else:
                        info_value = regex_infos.group(2)

                    infos[info_key] = info_value  # rajout dans un dictionnaire de l'info

        return infos

    def extract_raw_command(self) -> str:
        regex_match = re.search(r"^DECLARE[^;]*;[\s]*(.*)", self.content, re.MULTILINE | re.DOTALL)
        raw_cmd = regex_match.group(1) if regex_match is not None else self.content
        return raw_cmd


class SqlParam:
    def __init__(self, sql_declare: str):
        self.sql_declare = sql_declare

        app_settings: settings.Settings = settings.Settings()
        self.converter = SqlTypeConverter(
            date_display=app_settings.date_format,
            decimal_separator=app_settings.decimal_separator,
        )  # object to convert string to value or value to string

        self.var_name: str = ""
        self.type_name: str = ""
        self.type_args: list = []

        self.description: str = ""
        self.is_optional: bool = False
        self.is_hidden: bool = False
        self.ui_control: str = ""
        self.authorized_values: dict = {}
        self.ctrl_pattern: re.Pattern | None = None
        self.is_inlined: bool = False

        self.display_value: str = ""
        self.value_cmd: str | int | float = ""

        self.value_is_ok: bool = False
        self.ctrl_pattern_is_ok: bool = False

        self.func_dict: dict[str, Callable[..., str]] = self._funcs_init()

        self.set_default()

    # ------------------------------------------------------------------------------------------
    # Default value initialization
    # ------------------------------------------------------------------------------------------
    def set_default(self) -> None:
        match = VAR_NAME_PATTERN.search(self.sql_declare)
        self.var_name = match.group(0) if match else ""

        match = TYPE_NAME_PATTERN.search(self.sql_declare)
        self.type_name = match.group(1).lower() if match else ""
        self.type_args = self._get_type_args()

        match = DISPLAY_VALUE_PATTERN.search(self.sql_declare)
        self.display_value = match.group(1).replace("'", "").strip() if match else ""

        self._infos_from_comment()

        # Convert default display value to default format and validate with converter
        self.display_value = self.authorized_values.get(self.display_value, self.display_value)
        self.display_value = self.converter.to_display(self.type_name, self.display_value)

        # Update value_cmd to ensure value_is_ok is properly initialized
        self.update_value_cmd()

    def _get_type_args(self) -> list:
        str_type_args = TYPE_ARGS_PATTERN.search(self.sql_declare)
        str_type_args = str_type_args[1].lower().split(",") if str_type_args else []
        return [arg.strip() for arg in str_type_args]

    def _infos_from_comment(self) -> None:
        comment = COMMENT_PATTERN.search(self.sql_declare)
        if not comment:
            return

        comment_infos = comment[1].split("|")

        # description
        if comment_infos[0]:
            self.description = comment_infos[0]

        # optional information or UI control
        if len(comment_infos) > 1 and comment_infos[1]:
            infos = re.sub(r"(\(.*?\))|(,)", r"\1^^^", comment_infos[1])
            infos = [m.strip() for m in re.split(r"\^{3,6}", infos) if m.strip()]

            ui_controls = ("entry", "list", "check")
            calc_funcs = ("user_info", "fiscal_year", "month_end", "today")

            for info in infos:
                info_lst = INFO_LIST_PATTERN.search(info)
                if info_lst is None:
                    continue

                info_func = info_lst[1].lower()
                info_args = info_lst[2]

                if info_func == "optional":
                    self.is_optional = True
                elif info_func == "hide":
                    self.is_hidden = True
                elif info_func in calc_funcs:  # self.func_dict:
                    self.display_value = self._calc_func(info_func, info_args)
                elif info_func in ui_controls or info_func == "":
                    self.ui_control = info_func
                    self._authorized_values(info_func, info_args)

        # should parameter be hardcoded and not parameterized
        if self.var_name.startswith("@!"):
            allowed_for_inlined = ("list", "check")
            if self.ui_control not in allowed_for_inlined:
                raise ValueError(
                    f"{self.var_name} is not parameterized. " + f"Only {allowed_for_inlined} can be used as control"
                )

            self.is_inlined = True

        # ctrl pattern regex for display value validation
        if len(comment_infos) > 2 and comment_infos[2]:
            self.ctrl_pattern = re.compile(("|").join(comment_infos[2:]))

    def _authorized_values(self, ctrl: str, args: str) -> None:
        my_args = args.split(",")

        for i, arg in enumerate(my_args):
            key_val = arg.split(":")
            key = key_val[0].strip()

            if ctrl != "check":
                val = key_val[1].strip() if len(key_val) > 1 else key
            else:
                val = "on" if i == 0 else "off"

            self.authorized_values[key] = val

    # ------------------------------------------------------------------------------------------
    # Dynamic default value functions
    # ------------------------------------------------------------------------------------------
    def _funcs_init(self) -> dict[str, Callable[..., str]]:
        def user_info(attr: str) -> str:
            user: users.CurrentUser = users.CurrentUser()
            if attr in user.users.attribs_std:
                info = getattr(user, attr, "")
            else:
                info = user.attribs_cust.get(attr, "")

            return info

        def fiscal_year(
            last_month: int, month_offset: int = 0, days_offset: int = 0, today_mth_offset: int = 0
        ) -> str:
            last_month = int(last_month)
            month_offset = int(month_offset)
            days_offset = int(days_offset)
            today_mth_offset = int(today_mth_offset)

            date_ref = datetime.today() + relativedelta(months=today_mth_offset)
            my_year = date_ref.year
            my_year += 1 if date_ref.month > last_month else 0

            my_date = (
                datetime(my_year, last_month, 1)
                + relativedelta(months=month_offset + 1)
                + relativedelta(days=-1 + days_offset)
            )

            return datetime.strftime(my_date, self.converter.date_cmd_format)

        def month_end(month_offset: int = 0, days_offset: int = 0) -> str:
            month_offset = int(month_offset)
            days_offset = int(days_offset)

            my_date = datetime(datetime.today().year, datetime.today().month, 1)
            my_date += relativedelta(months=month_offset + 1) + relativedelta(days=-1 + days_offset)

            return datetime.strftime(my_date, self.converter.date_cmd_format)

        def today(days_offset: int = 0) -> str:
            days_offset = int(days_offset)

            my_date = datetime.today()
            my_date += relativedelta(days=days_offset)

            return datetime.strftime(my_date, self.converter.date_cmd_format)

        return {"user_info": user_info, "fiscal_year": fiscal_year, "month_end": month_end, "today": today}

    def _calc_func(self, func: str, func_args: str) -> str:
        my_args = func_args.lower().split(",") if func_args else []
        my_func = self.func_dict.get(func)

        if callable(my_func):
            return my_func(*my_args)

        return self.display_value

    # ------------------------------------------------------------------------------------------
    # Display value conversion and validation
    # ------------------------------------------------------------------------------------------
    def update_value_cmd(self) -> str | int | float:
        """
        Updates value_cmd from display_value.

        Validates that value is within authorized values and provided if mandatory (blocking).
        Tests ctrl_pattern match (non-blocking, for UI warning only).

        Returns:
            Converted value for SQL command
        Raises:
            ValueError: if validation fails or ctrl_pattern regex is invalid
        """

        self.value_cmd = ""
        self.ctrl_pattern_is_ok = False
        self.value_is_ok = False

        self.value_cmd = self.convert_display_value_to_cmd(self.display_value)  # ValueError if validation fails
        self.ctrl_pattern_is_ok = self.test_ctrl_pattern(self.display_value)  # ValueError if ctrl_pattern invalid

        # update display_value for date to default format
        if self.type_name in ("date", "datetime") and self.display_value != "":
            self.display_value = self.converter.to_display(self.type_name, self.value_cmd)

        self.value_is_ok = True

        return self.value_cmd

    def convert_display_value_to_cmd(self, val_to_convert: str) -> str | int:
        """ValueError: if value is not valid, or missing and not optional"""

        if self.authorized_values:
            if val_to_convert not in self.authorized_values.values():
                raise ValueError(
                    f"{val_to_convert}, ne fait pas partie des valeurs autorisées : "
                    + ", ".join(self.authorized_values.values())
                )
            else:
                val_to_convert = [k for k, v in self.authorized_values.items() if v == val_to_convert][0]

        if not val_to_convert and not self.is_optional:
            raise ValueError(f"paramètre obligatoire pour {self.var_name}")

        return self.converter.to_cmd(self.type_name, val_to_convert, self.type_args)

    def test_ctrl_pattern(self, val_to_test: str) -> bool:
        """ValueError: if ctrl_pattern is not a valid one"""

        if not val_to_test or not self.ctrl_pattern:
            return True

        try:
            if self.ctrl_pattern.match(val_to_test):
                return True
        except re.error as e:
            raise ValueError(f"Regex invalide pour {self.var_name} : {self.ctrl_pattern.pattern}") from e

        return False


class SqlParameters:
    def __init__(self, file_content: str):
        self.params: dict[str, SqlParam] = self._parse_declare(file_content)

    def _parse_declare(self, file_content: str) -> dict[str, SqlParam]:
        regex_match = DECLARE_EXTRACT_PATTERN.search(file_content)  # section DECLARE

        params_dict = {}
        params_txt = regex_match.group(1) if regex_match is not None else ""
        for line in params_txt.splitlines():
            if line[0:1] == "@":
                param = SqlParam(line)
                params_dict[param.var_name] = param

        return params_dict

    def update_values(self, key: str | None = None) -> bool:
        """
        If key is none then update all key
        """
        if key is not None and key not in self.params:
            return False

        my_list = [key] if key is not None else self.params.keys()

        for key in my_list:
            self.params[key].update_value_cmd()

        return True

    def validate(self, key: str | None = None) -> bool:
        """
        If key is none then update all key
        """
        if key is not None and key not in self.params:
            return False

        my_list = [key] if key is not None else self.params.keys()

        for key in my_list:
            if not self.params[key].value_is_ok:
                return False

        return True

    def reset_values(self):
        for _, param in self.params.items():
            param.set_default()


class CmdBuilder:
    def __init__(self, raw_cmd: str, parameters: SqlParameters):
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
            prefs: UserPrefs = UserPrefs()
            extract_file = prefs.get(PrefsEnum.cache_folder) / (f"{query_name}_{file_stamp}.csv")

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
            extract_encoding=settings.Settings().extract_encoding,
            params_metadata=params_metadata,
        )

    def build_command(self) -> tuple[str, dict]:
        """Construit la commande finale avec remplacement des @! variables"""
        cmd_template = self.template
        cmd_parameters = {}

        for key, param in self.parameters.params.items():
            if param.is_inlined:
                cmd_template = cmd_template.replace(key, str(param.value_cmd))
            else:
                cmd_parameters[key] = param.value_cmd

        return cmd_template, cmd_parameters

    def build_debug_command(self) -> str:
        """Retourne la commande pour debug"""
        cmd_debug = self.template
        for key, param in self.parameters.params.items():
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


class QueryExecute:
    def __init__(self):
        self.converter = SqlTypeConverter()

        self.config: ExecuteConfig | None = None
        self.server: servers.Server | None = None

        self.app_settings: settings.Settings = settings.Settings()
        self.field_separator = self.app_settings.field_separator
        self.print_date_format = PRINT_DATE_FORMAT

        self.queue: Queue = Queue()  # pour transmettre les messages à l'UI
        self.stop_requested: ProcEvent = proc_get_event()  # pour signaler que l'execution doit être stoppée
        self.cannot_stop: ProcEvent = proc_get_event()  # pour savoir si l'execution ne peut pas stopper proprement

    def load_infos(self, config: ExecuteConfig) -> bool:
        # controle validité du fichier d'extraction
        if config.extract_file != Path() and config.extract_file.exists():
            raise FileExistsError(f"Le fichier {config.extract_file} existe déjà.")

        # controle validité de server_id
        if not self.get_server(config.server_id):
            return False

        # controle qu'une commande existe bien
        if config.cmd_template == "":
            return False

        self.config = config

        return True

    def get_server(self, server_id) -> servers.Server | None:
        try:
            self.server = servers.Servers().servers_dict[server_id]
        except KeyError:
            error_msg = f"Infos du serveur non trouvé, id : {server_id}"
            self._broadcast(self._time_log() + f" - {error_msg}")
            return None

        return self.server

    def execute(self, config: ExecuteConfig) -> tuple[int, Path | list[list[str]] | None]:
        if not self.load_infos(config):
            return (-1, None)
        if self.stop_requested.is_set():
            return (-1, None)

        if self.server is None or self.config is None:
            return (-1, None)

        starting_date = datetime.now()
        self._broadcast(starting_date.strftime(self.print_date_format) + " - Connexion à la base de données...")

        self.cannot_stop.set()
        with self.server.get_connection() as conn, conn.cursor() as cursor:
            # execution de la requête et gestion des erreurs liées
            self._broadcast(self._time_log() + " - Requête en cours d'execution...")
            self.queue.put(("start_timer", ""))
            try:
                cursor.execute(self.config.cmd_template, self.config.cmd_parameters)
                self.cannot_stop.clear()
                self.queue.put(("stop_timer", ""))
            except (pymssql._pymssql.ProgrammingError, psycopg2.ProgrammingError) as err:
                self._broadcast(self._time_log() + f" - Erreur SQL : {str(err)}")
                return (-1, None)
            except Exception as err:
                error_type = type(err).__name__
                self._broadcast(self._time_log() + f" - Erreur d'execution inattendue ({error_type}) :\n{str(err)}")
                return (-1, None)

            # si pas de fichier d'extraction alors renvoi des données dans une liste
            if self.config.extract_file == Path():
                rows_count, execute_output = self._extract_to_list(cursor)
                return rows_count, execute_output

            # sinon extraction des données dans un fichier
            try:
                self._broadcast(self._time_log() + " - Début récupération des lignes...")
                rows_count, execute_output = self._extract_to_file(cursor, config.extract_encoding)
                if execute_output == "" and self.stop_requested.is_set():
                    raise InterruptedError()
                ending_date = datetime.now()
                self._execute_end(starting_date, ending_date, rows_count)
                return rows_count, execute_output
            except InterruptedError:
                self._broadcast(self._time_log() + " - Extraction interrompue")
            except PermissionError:
                self._broadcast(self._time_log() + f" - Ecriture refusée pour : {self.config.extract_file}")
            except (FileNotFoundError, OSError) as e:
                error_msg = str(e).split("]")[1].strip() if "]" in str(e) else str(e)
                self._broadcast(
                    self._time_log()
                    + f" - Erreur ouverture de : {self.config.extract_file.name}\n"
                    + f"Détail : {error_msg}"
                )

        # si erreur lors de l'extraction dans un fichier alors suppression du fichier créé
        if self.config.extract_file != Path():
            self.config.extract_file.unlink(missing_ok=True)

        return (-1, None)

    def _execute_end(self, starting_date: datetime, ending_date: datetime, rows_count: int):
        assert self.server is not None and self.config is not None

        # writing user log
        user_log: logs_user.UserDb = logs_user.UserDb()
        user_log.insert_exec(
            self.server.id,
            self.config.query_name,
            starting_date,
            ending_date,
            rows_count,
            self.config.params_metadata,
            self.config.extract_file,
        )

        # if on, launch central log
        if self.app_settings.logs_are_on:
            user: users.CurrentUser = users.CurrentUser()
            log_infos: dict = {"user_db": user_log.user_db, "user_id": user.username, "user_name": user.title}
            self.queue.put(("central_log", log_infos))

        # notify end result
        elapsed_time = (ending_date - starting_date).total_seconds()
        self._broadcast(
            str("=") * 50
            + "\nLigne(s) extraite(s) : {:,}".format(rows_count).replace(",", " ")
            + f"\nDébut : {starting_date.strftime(self.print_date_format)}"
            + f"\nFin : {ending_date.strftime(self.print_date_format)}"
            + f"\nDurée : {int(elapsed_time) // 60:02d}:{int(elapsed_time) % 60:02d}"
            + f"\n{str('=') * 50}"
        )

    def _extract_to_list(self, cursor) -> tuple[int, list[list[str]]]:
        buffer = []
        row_number = -1

        # ajout entête au buffer
        row_headers = [colname[0] for colname in cursor.description]
        buffer.append(row_headers)

        for row_number, record in enumerate(cursor):
            # si arrêt demandé, sortie
            if self.stop_requested.is_set():
                return 0, []

            # ajout enregistrement courant au buffer
            row_content = self._sql_record_to_text(record)
            buffer.append(row_content)

        return row_number + 1, buffer

    def _extract_to_file(self, cursor, encoding: str) -> tuple[int, Path | None]:
        buffer = []  # buffer pour stocker les lignes avant écriture dans le fichier
        buffer_max_size = 50_000  # nombre de lignes pour déclencher écriture dans fichier
        row_number = -1

        if self.config is None or self.config.extract_file == Path():
            raise ValueError("Extraction to file is not possible without specifying one")

        try:
            codecs.lookup(encoding)
        except LookupError:
            encoding = "utf-8-sig"
            self._broadcast(
                self._time_log() + f" - Encodage pour extraction invalide, utilisation '{encoding}' par défaut"
            )

        self.config.extract_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config.extract_file, mode="w", encoding=encoding, errors="replace", newline="") as f:
            csv_writer = csv.writer(f, delimiter=self.field_separator, quotechar='"', quoting=csv.QUOTE_MINIMAL)

            # ajout entête au buffer
            buffer.append([colname[0] for colname in cursor.description])

            for row_number, record in enumerate(cursor):
                # si arrêt demandé, stopper l'extraction
                if self.stop_requested.is_set():
                    return 0, None

                # ajout enregistrement courant au buffer
                buffer.append(self._sql_record_to_text(record))

                # écriture du buffer si plein
                if (row_number + 1) % buffer_max_size == 0:
                    self._broadcast(
                        self._time_log() + " - Ecriture ligne : {:,}...".format(row_number + 1).replace(",", " ")
                    )
                    csv_writer.writerows(buffer)
                    buffer.clear()

            # écriture du buffer si non vide et si ce n'est pas que l'entête
            if buffer and row_number != -1:
                self._broadcast(self._time_log() + " - Ecriture des dernières lignes...")
                csv_writer.writerows(buffer)
                buffer.clear()

        self._broadcast(self._time_log() + " - Ecriture finie")

        if row_number == -1:
            self.config.extract_file.unlink(missing_ok=True)

        return row_number + 1, self.config.extract_file

    def _sql_record_to_text(self, record) -> list:
        if self.server is None:
            raise RuntimeError("Server must be initialized before converting records")

        # mise en cache des éléments de conversion
        charset = self.server.charset
        convert_func = self.converter.from_result

        def clean_value(val):
            value_txt = convert_func(val)

            if isinstance(val, str):
                try:
                    return value_txt.encode(charset).decode("utf-8")
                except (UnicodeDecodeError, UnicodeEncodeError, LookupError):
                    return value_txt

            return value_txt

        return [clean_value(val) for val in record]

    def _broadcast(self, msg_to_display: str, msg_type: str = "msg_output") -> None:
        self.queue.put((msg_type, msg_to_display))
        print(msg_to_display)

    def _time_log(self) -> str:
        return datetime.now().strftime(self.print_date_format)


def get_queries(folder: Path) -> tuple[list[Query], list[str]]:
    if not Path(folder).is_dir():
        raise ValueError(f"Erreur : le répertoire {Path(folder)} n'a pas été trouvé ou n'est pas accessible !")

    queries: list[Query] = []
    errors: list[str] = []

    files = list(Path(folder).glob("*.sql"))
    for file in files:
        try:
            my_query = Query(Path(file))
        except ValueError as e:
            error = f"Erreur chrgmt '{Path(file).name}' : ValueError, {e}"
            errors.append(error)
            print(error)
            continue
        except Exception as e:
            error = f"Erreur chrgmt '{Path(file).name}' : {e.__class__.__name__}, {e}"
            errors.append(error)
            print(error)
            continue  # si erreur, ne pas bloquer et ignorer la requête

        queries.append(my_query)

    queries.sort(key=lambda k: k.name)

    return queries, errors


def filter_queries(queries: list[Query], server_id: str, user: users.CurrentUser = users.CurrentUser()) -> list[Query]:
    filtered: list[Query] = []

    # check if user is authorized for server
    all_servers: servers.Servers = servers.Servers()
    server: servers.Server | None = all_servers.servers_dict.get(server_id, None)
    if server is None:
        return []
    if not user.admin:
        if server.grp_authorized != [] and not set(user.grp_authorized) & set(server.grp_authorized):
            return []

    # check which query user can see
    for query in queries:
        # ignore query if it's not for server_id
        if server_id not in query.servers_id:
            continue

        # ignore query if user not allowed to access it
        if (
            not user.admin
            and not query.grp_authorized == []
            and not set(user.grp_authorized) & set(query.grp_authorized)
        ):
            continue

        # finally check hidden property to determine if query must be listed
        if query.hide == 0 or (user.admin and query.hide != 2):
            filtered.append(query)

    return filtered


def orphan_queries(folder: Path) -> list[Path]:
    if not Path(folder).is_dir():
        raise ValueError(f"Erreur : le répertoire {folder} n'a pas été trouvé ou n'est pas accessible !")

    orphan_files: list[Path] = []

    all_servers: servers.Servers = servers.Servers()
    all_ids = all_servers.servers_dict.keys()

    files = list(Path(folder).glob("*.sql"))
    for file in files:
        try:
            query = Query(Path(file))
            if not set(query.servers_id) & set(all_ids):
                orphan_files.append(Path(file))
                print(f"Aucun serveur existant pour : {Path(file).name}")
        except Exception as e:
            error = f"Erreur chrgmt '{Path(file).name}' : {e.__class__.__name__}, {e}"
            print(error)
            continue  # si erreur, ne pas bloquer et ignorer la requête

    return orphan_files


if __name__ == "__main__":
    app_settings: settings.Settings = settings.Settings()
    APP_PATH = app_settings.app_path

    # queries, errors = get_queries(Path(app_settings.queries_folder))

    sql_script = list(Path(app_settings.queries_folder).glob("*.sql"))[0]
    sql_script = Path(app_settings.queries_folder) / "ZCOMP.sql"

    my_query = Query(sql_script)
    my_query.update_values()
    print(my_query.get_exec_config())
    print(my_query.get_cmd_for_debug())
    my_query.execute(file_output=True)

    orphan_queries(settings.Settings().queries_folder)
