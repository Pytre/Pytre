import re
from datetime import datetime
from dateutil.relativedelta import relativedelta
from pathlib import Path
from threading import Event

import pymssql

import settings
import user_prefs
import users
import servers
import logs_user
import logs_central
from convert import Convert
from about import APP_NAME, APP_VERSION


SETTINGS: settings.Settings = settings.Settings()
PRINT_DATE_FORMAT: str = "%d/%m/%Y à %H:%M:%S"  # pour le format de la date pour les logs / output
USER_PREFS: user_prefs.UserPrefs = user_prefs.UserPrefs()
USER: users.User = users.User()
SERVERS: servers.Servers = servers.Servers()
SERVER: servers.Server = servers.Server()
USER_LOG: logs_user.UserDb = logs_user.UserDb()
CENTRAL_LOGS: logs_central.FileDriven = logs_central.FileDriven(SETTINGS.logs_folder)


class Query:
    def __init__(self, filename: Path = Path("dummy"), encoding_format: str = "utf-8"):
        self.query_execute = _QueryExecute(self)

        self.last_extracted_file = ""  # info du dernier fichier extrait
        self.msg_list: list[str] = []  # liste de msg à l'utilisateur pour interface graphique
        self.exec_ask_stop: Event = Event()  # pour signaler que l'execution doit être stoppée
        self.exec_can_stop: Event = Event()  # pour savoir si l'execution peut stopper proprement

        self.filename = filename
        self.file_content = self._init_file_content(encoding_format)

        self.raw_cmd = self._init_raw_cmd()
        self.cmd_template = self._init_template_cmd()
        self.cmd_params = {}

        self.infos: dict = self._init_infos()
        self.params_obj: dict[str, _Param] = self._init_params()

        self.name: str = self.infos.get("code", self.filename.stem)

        hide: str = self.infos.get("hide", "0")
        self.hide: int = int(hide) if hide.isdigit() else 0
        if self.hide:
            self.description = "(" + "*" * self.hide + ") " + self.infos.get("description", "")
        else:
            self.description = self.infos.get("description", "")

        self.grp_authorized: list[str] = self.infos.get("grp_authorized", [])
        self.servers_id: list[str] = self.infos.get("servers", [SERVERS.default_id])

    def _init_file_content(self, encoding_format: str = "utf-8") -> str:
        file_content = ""
        try:
            with open(self.filename, mode="r", encoding=encoding_format) as f:
                file_content = f.read()
        except FileNotFoundError:
            pass
        except UnicodeDecodeError:
            alternative_encoding_format = "ansi" if encoding_format == "utf-8" else "utf-8"
            with open(self.filename, mode="r", encoding=alternative_encoding_format) as f:
                file_content = f.read()

        return file_content

    def _init_infos(self) -> dict:
        infos = {}
        regex_match = re.search(
            r"^\/\*[^\n]*?\n(.*?)\n^\*\/", self.file_content, re.MULTILINE | re.DOTALL
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

    def _init_params(self) -> dict:
        regex_match = re.search(r"^(DECLARE[^;]*;)", self.file_content, re.MULTILINE | re.DOTALL)  # section DECLARE

        params = {}
        params_txt = regex_match.group(1) if regex_match is not None else ""
        for line in params_txt.splitlines():
            if line[0:1] == "@":
                my_param = _Param(line)
                params[my_param.var_name] = my_param
                self.cmd_params[my_param.var_name] = my_param.value_cmd

        return params

    def _init_raw_cmd(self) -> str:
        regex_match = re.search(r"^DECLARE[^;]*;[\s]*(.*)", self.file_content, re.MULTILINE | re.DOTALL)
        raw_cmd = regex_match.group(1) if regex_match is not None else self.file_content
        return raw_cmd

    def _init_template_cmd(self) -> str:
        cmd_template = re.sub(r"(?<![\d\w#_\$@])(@[\d\w#_\$@]+)", r"%(\1)s", self.raw_cmd)
        return cmd_template

    def reset_values(self):
        for k, v in self.params_obj.items():
            v.reset_param()
            self.cmd_params[k] = ""

    def update_values(self, key: str = None) -> bool:
        """
        If key is none then update all key
        """
        my_list = [key] if key is not None else self.params_obj.keys()

        for key in my_list:
            param = self.params_obj[key]
            self.cmd_params[key] = param.update_value_cmd()

        return True

    def values_ok(self, key: str = None) -> bool:
        my_list = [key] if key is not None else self.params_obj.keys()

        for key in my_list:
            if not self.params_obj[key].value_is_ok:
                return False

        return True

    def execute_cmd(self, file_output: bool = True) -> bool | tuple:
        self.msg_list = []
        self.last_extracted_file = ""
        self.update_values()

        if self.values_ok():
            cmd_exec, cmd_params = self.get_infos_for_exec()
            self.query_execute.cmd_template = cmd_exec
            self.query_execute.cmd_parameters = cmd_params
            if file_output:
                file_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                extract_file = USER_PREFS.extract_folder / (f"{self.name}_{file_stamp}.csv")
            else:
                extract_file = ""

            try:
                result = self.query_execute.execute(extract_file)
                self.last_extracted_file = extract_file if file_output else ""
                return result
            except pymssql._pymssql.OperationalError as err:
                err_msg = str("=") * 50 + "\n"
                err_msg += err.args[0][1].decode("utf-8") + "\n"
                self._broadcast(err_msg)
                return False
        else:
            err_msg = "\nImpossible d'executer des paramètres sont invalides"
            self._broadcast(err_msg)
            return False

    def get_infos_for_exec(self):
        cmd_exec = self.cmd_template
        cmd_params = {}
        for k, v in self.cmd_params.items():
            if k[0:2] != "@!":  # si une variable
                cmd_params[k] = v
            else:  # sinon valeur à remplacer en dur
                cmd_exec = re.sub(rf"{k}", rf"{v}", cmd_exec)

        return cmd_exec, cmd_params

    def get_cmd_for_debug(self):
        cmd_debug = self.cmd_template
        for k, v in self.cmd_params.items():
            if k[0:2] == "@!":  # pas une variable
                cmd_debug = re.sub(rf"{k}", rf"{v}", cmd_debug)
                continue
            elif isinstance(v, str):
                value = "'" + v.replace("'", "''") + "'"
            else:
                value = str(v)

            cmd_debug = re.sub(rf"%\({k}\)s", rf"{value}", cmd_debug)

        return cmd_debug

    def _broadcast(self, msg_to_broadcast: str) -> None:
        self.msg_list.append(msg_to_broadcast)
        print(msg_to_broadcast)


class _Param:
    def __init__(self, sql_declare: str):
        self.converter = Convert(
            date_txt_format=SETTINGS.date_format,
            field_separator=SETTINGS.field_separator,
            decimal_separator=SETTINGS.decimal_separator,
        )  # objet pour conversions string en valeurs et l'inverse
        self.sql_declare = sql_declare
        self.set_default()

    def set_default(self) -> None:
        self.var_name = re.search(r"^@!?[\d\w#_\$@]+", self.sql_declare)[0]
        self.type_name = re.search(r"^[^(=]+as\s+([^(\s=]+)", self.sql_declare, re.IGNORECASE)[1].lower()
        self.type_args = self._get_type_args()
        self.display_value = re.search(r"^[^=]*=\s*('.*?'|[^,\s]*)", self.sql_declare)[1].replace("'", "").strip()

        # infos additionnelles
        self.description = ""
        self.is_optional = False
        self.is_hidden = False
        self.value_cmd = ""
        self.value_is_ok = False
        self.ui_control = ""
        self.authorized_values = {}

        self._infos_from_comment()

        # conversion valeur affichage défaut pour vérifier si bien ok
        self.display_value = self.authorized_values.get(self.display_value, self.display_value)
        self.display_value = self.converter.to_display(self.type_name, self.display_value)

        # mettre à jour la val pour cmd => sinon risque que value_is_ok ne soit jamais mis à jour
        self.update_value_cmd()

    def _get_type_args(self) -> list:
        str_type_args = re.search(r"^[^(=]+\(([^)]+)\)", self.sql_declare)
        str_type_args = str_type_args[1].lower().split(",") if str_type_args else []
        return [arg.strip() for arg in str_type_args]

    def _infos_from_comment(self) -> None:
        comment = re.search(r"--\s*(.*?)(?=\||$)\|?(.*$)", self.sql_declare)
        if comment[1]:
            self.description = comment[1]

        if comment[2]:
            infos = re.sub(r"(\(.*?\))|(,)", r"\1^^^", comment[2])
            infos = [m.strip() for m in re.split(r"\^{3,6}", infos) if m.strip()]

            ui_funcs = ("entry", "list", "check")
            calc_funcs = ("user_info", "fiscal_year", "month_end", "today")

            for info in infos:
                info_lst = re.search(r"^(.*?)(?=\(|$)\(?(.*?)\)?$", info)
                info_func = info_lst[1].lower()
                info_args = info_lst[2]

                if info_func == "optional":
                    self.is_optional = True
                elif info_func == "hide":
                    self.is_hidden = True
                elif info_func in calc_funcs:  # self.func_dict:
                    self.display_value = self._calc_func(info_func, info_args)
                elif info_func in ui_funcs or info_func == "":
                    self.ui_control = info_func
                    self._authorized_values(info_func, info_args)

    def _calc_func(self, func: str, func_args: str) -> str:
        def user_info(attr: str) -> str:
            if attr in USER.users.attribs_std:
                info = getattr(USER, attr, "")
            else:
                info = USER.attribs_cust.get(attr, "")

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

            return datetime.strftime(my_date, self.converter.date_val_format)

        def month_end(month_offset: int = 0, days_offset: int = 0) -> str:
            month_offset = int(month_offset)
            days_offset = int(days_offset)

            my_date = datetime(datetime.today().year, datetime.today().month, 1)
            my_date += relativedelta(months=month_offset + 1) + relativedelta(days=-1 + days_offset)

            return datetime.strftime(my_date, self.converter.date_val_format)

        def today(days_offset: int = 0) -> str:
            days_offset = int(days_offset)

            my_date = datetime.today()
            my_date += relativedelta(days=days_offset)

            return datetime.strftime(my_date, self.converter.date_val_format)

        self.func_dict = {"user_info": user_info, "fiscal_year": fiscal_year, "month_end": month_end, "today": today}

        my_args = func_args.lower().split(",") if func_args else None
        my_str = self.func_dict.get(func)(*my_args) if func in self.func_dict else self.display_value

        return my_str

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

    def update_value_cmd(self) -> str | int | float:
        self.value_is_ok = False
        self.value_cmd = ""
        val_to_test = self.display_value

        if self.authorized_values != {}:
            if val_to_test not in self.authorized_values.values():
                raise ValueError(
                    f"{val_to_test}, ne fait pas partie des valeurs autorisées : "
                    + ", ".join(self.authorized_values.values())
                )
            else:
                val_to_test = [k for k, v in self.authorized_values.items() if v == val_to_test][0]

        if not val_to_test and not self.is_optional:
            raise ValueError(f"paramètre obligatoire pour {self.var_name}")
        else:
            self.value_cmd = self.converter.to_cmd(self.type_name, val_to_test, self.type_args)

            # modif valeur affichage pour dates qui peuvent être saisie sur un format different que voulue
            if self.type_name in ("date", "datetime") and self.display_value != "":
                self.display_value = self.converter.to_display(self.type_name, self.value_cmd)

        self.value_is_ok = True

        return self.value_cmd


class _QueryExecute:
    def __init__(self, parent: Query):
        self.converter = Convert()
        self.parent = parent  # pour retourner des messages pour l'interface graphique

        self.cmd_template = ""
        self.cmd_parameters = {}
        self.extract_file = ""

        self.field_separator = SETTINGS.field_separator
        self.print_date_format = PRINT_DATE_FORMAT

        self.sql_server_params: dict = self.get_servers_params()

    def get_servers_params(self) -> dict:
        return {
            "server": SERVER.server,
            "host": SERVER.host,
            "user": SERVER.user,
            "password": SERVER.password,
            "database": SERVER.database,
            "timeout": SERVER.timeout,
            "login_timeout": SERVER.login_timeout,
            "charset": SERVER.charset,
            "as_dict": False,
            "port": SERVER.port,
            "read_only": True,
            "appname": f"{APP_NAME}_{APP_VERSION}",
        }

    def execute(self, extract_file):
        self.extract_file = extract_file

        if self.cmd_template == "":
            return False

        starting_date = datetime.now()
        self._broadcast(starting_date.strftime(self.print_date_format) + " - Connection à la base de données...")

        self.parent.exec_can_stop.clear()
        with pymssql.connect(**self.sql_server_params) as conn:
            with conn.cursor() as cursor:
                self._broadcast(self._time_log() + " - Requête en cours d'execution...")
                try:
                    cursor.execute(self.cmd_template, self.cmd_parameters)
                    self.parent.exec_can_stop.set()
                except pymssql._pymssql.ProgrammingError as err:
                    error_code = err.args[0]
                    error_msg = str(err.args[1])[2:-2]
                    self._broadcast(self._time_log() + f" - Erreur {error_code} : {error_msg}")
                    return False
                except Exception as err:
                    self._broadcast(self._time_log() + f" - Erreur : {err}")
                    return False

                self._broadcast(self._time_log() + " - Début récupération des lignes...")
                rows_count, execute_output = self._extract_to_file(cursor)

        ending_date = datetime.now()
        self._execute_end(starting_date, ending_date, rows_count)

        return rows_count, execute_output

    def _execute_end(self, starting_date: datetime, ending_date: datetime, rows_count: int):
        params_for_log = self._params_for_log()
        USER_LOG.insert_exec(
            self.parent.name, starting_date, ending_date, rows_count, params_for_log, self.extract_file
        )

        if SETTINGS.logs_are_on:
            CENTRAL_LOGS.trigger_sync(USER_LOG.user_db, USER.username, USER.title)

        self._broadcast(
            str("=") * 50
            + "\nLigne(s) extraite(s) : "
            + "{:,}".format(rows_count).replace(",", " ")
            + f"\nDébut : {starting_date.strftime(self.print_date_format)}"
            + f"\nFin : {ending_date.strftime(self.print_date_format)}"
        )
        if rows_count > 0:
            self._broadcast(f"Fichier extrait : {self.extract_file}\n" + str("=") * 50)
        else:
            self._broadcast("Fichier extrait : aucune ligne de récupérée, pas de fichier\n" + str("=") * 50)

    def _extract_to_file(self, cursor):
        if cursor is None:
            return 0, ""

        buffer_block_size = 50000  # nombre de lignes pour déclencher écriture dans fichier
        buffer = []

        line_header = self.field_separator.join(
            [colname[0] for colname in cursor.description]
        )  # ligne des entêtes de colonnes
        buffer.append(line_header)  # stockage des entêtes de colonne dans le buffer

        row_number = None
        for row_number, record in enumerate(cursor):  # parcours résultats
            line_buffer = self._sql_record_to_text(record)
            buffer.append(line_buffer)
            if (
                self.extract_file != "" and row_number != 0 and (row_number + 1) % buffer_block_size == 0
            ):  # écriture dans le fichier par blocs
                block_start = row_number + 1 - buffer_block_size + 1
                block_end = row_number + 1
                self._broadcast(
                    self._time_log()
                    + " - Ecriture lignes "
                    + "{:,}".format(block_start).replace(",", " ")
                    + " à "
                    + "{:,}".format(block_end).replace(",", " ")
                    + "..."
                )
                self._file_write(buffer)
                buffer.clear()

            if self.parent.exec_ask_stop.is_set():
                return 0, ""

        if self.extract_file != "" and len(buffer) > 1:  # si buffer (et pas que entête) alors écrire ce qui reste
            self._broadcast(self._time_log() + " - Ecriture des dernières lignes...")
            self._file_write(buffer)
            buffer.clear()

        self._broadcast(self._time_log() + " - Ecriture finie")
        row_number = row_number + 1 if row_number is not None else 0

        if self.extract_file != "":
            return row_number, self.extract_file
        else:
            return row_number, buffer

    def _sql_record_to_text(self, record):
        line_buffer = ""

        for j in range(len(record)):  # récup champs et conversion valeur récupérée en texte pour export
            value = record[j]
            value_txt = self.converter.from_result(value)
            line_buffer += value_txt
            if j != len(record) - 1:
                line_buffer += self.field_separator

        return line_buffer

    def _broadcast(self, msg_to_display: str) -> None:
        self.parent._broadcast(msg_to_display)

    def _file_write(self, list_to_write: list[str], encoding_format: str = "latin-1") -> None:  # Ecrire texte
        filename = self.extract_file
        try:
            with open(filename, mode="a", encoding=encoding_format) as f:
                f.write("\n".join(list_to_write) + "\n")  # écriture du buffer
        except UnicodeEncodeError:
            with open(filename, mode="a", encoding="utf-8") as f:
                f.write("\n".join(list_to_write) + "\n")  # écriture du buffer

    def _time_log(self) -> str:
        return datetime.now().strftime(self.print_date_format)

    def _params_for_log(self) -> dict:
        params: dict = {}
        param: _Param
        for k, param in self.parent.params_obj.items():
            params[k] = {
                "description": param.description,
                "val_cmd": param.value_cmd,
                "val_display": param.display_value,
            }

        return params


def get_queries(folder) -> list[Query]:
    if not Path(folder).is_dir():
        raise ValueError(f"Erreur : le répertoire {folder} n'a pas été trouvé ou n'est pas accessible !")

    queries: list[Query] = []
    errors: list[str] = []
    for file in Path(folder).iterdir():
        if file.is_file() and file.suffix == ".sql":
            try:
                my_query = Query(file)
            except ValueError as e:
                error = f"Erreur chrgmt '{file.name}' : ValueError, {e}"
                errors.append(error)
                print(error)
                continue
            except Exception as e:
                error = f"Erreur chrgmt '{file.name}' : {e.__class__.__name__}, {e}"
                errors.append(error)
                print(error)
                continue  # si erreur, ne pas bloquer et ignorer la requête

            user_is_authorized = False
            if my_query.grp_authorized == [] or set(USER.grp_authorized) & set(my_query.grp_authorized):
                user_is_authorized = True

            if (USER.admin and my_query.hide != 2) or (user_is_authorized and my_query.hide == 0):
                queries.append(my_query)

    queries.sort(key=lambda k: k.name)

    return queries, errors


if __name__ == "__main__":
    APP_PATH = SETTINGS.app_path
    sql_scripts = Path(SETTINGS.queries_folder).glob("*.sql")
    sql_script = ""
    for sql_script in sql_scripts:
        break

    my_query = Query(sql_script)
    my_query.update_values()
    print(my_query.get_infos_for_exec())
    print(my_query.get_cmd_for_debug())
    print(my_query.cmd_params)
    my_query.execute_cmd(file_output=True)
