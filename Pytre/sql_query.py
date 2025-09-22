import builtins
import re
import csv
from datetime import datetime
from dateutil.relativedelta import relativedelta
from pathlib import Path
from threading import Thread
from multiprocessing import Process, Queue, Event as proc_get_event
from multiprocessing.synchronize import Event as ProcEvent

import pymssql
import psycopg2

import settings
import user_prefs
import users
import servers
import logs_user
from convert import Convert


PRINT_DATE_FORMAT: str = "%d/%m/%Y à %H:%M:%S"  # pour le format de la date pour les logs / output


class Query:
    def __init__(self, filename: Path = Path("dummy"), encoding_format: str = "utf-8"):
        self.query_execute = _QueryExecute(self)

        self.last_extracted_file = ""  # info du dernier fichier extrait

        self.queue: Queue = Queue()  # pour transmettre les messages à l'UI
        self.force_stop: ProcEvent = proc_get_event()  # pour signaler que l'execution doit être stoppée
        self.can_stop: ProcEvent = proc_get_event()  # pour savoir si l'execution peut stopper proprement

        self.filename = filename
        self.file_content = self._init_file_content(encoding_format)

        self.infos: dict = self._init_infos()
        self.cmd_params = {}
        self.params_obj: dict[str, _Param] = self._init_params()

        self.raw_cmd = self._init_raw_cmd()
        self.cmd_template = self._init_template_cmd()

        self.name: str = self.infos.get("code", self.filename.stem)

        hide: str = self.infos.get("hide", "0")
        self.hide: int = int(hide) if hide.isdigit() else 0

        self.description: str = ""
        if self.hide:
            self.description = "(" + "*" * self.hide + ") " + self.infos.get("description", "")
        else:
            self.description = self.infos.get("description", "")

        self.grp_authorized: list[str] = self.infos.get("grp_authorized", [])
        all_servers_id = list(servers.Servers().servers_dict.keys())
        self.servers_id: list[str] = self.infos.get("servers", all_servers_id)

    def _init_file_content(self, encoding_format: str = "utf-8") -> str:
        file_content = ""

        # if filename has not been set then immediatly return without error
        if self.filename.name == "dummy":
            return file_content

        # set alternative encoding to try if failing to decode
        encodings: list = ["utf-8", "cp1252", "ansi"]
        encodings.remove(encoding_format) if encoding_format in encodings else None
        encodings.insert(0, encoding_format)

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
        def replace_func(match: re.Match):
            # replace only variables which are parameters
            if match.group(0) in self.cmd_params.keys():
                return f"%({match.group(0)})s"
            else:
                return match.group(0)

        cmd_template = re.sub(r"(?<![\d\w#_\$@])(@[\d\w#_\$@]+)", replace_func, self.raw_cmd)
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

    def execute_cmd(self, file_output: bool = True, server_id: str = "") -> bool | tuple:
        self.last_extracted_file = ""
        self.update_values()

        if self.values_ok():
            server_id = server_id or (self.servers_id[0] if self.servers_id else "")
            cmd_exec, cmd_params = self.get_infos_for_exec()

            if file_output:
                file_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                prefs: user_prefs.UserPrefs = user_prefs.UserPrefs()
                extract_file = prefs.extract_folder / (f"{self.name}_{file_stamp}.csv")
            else:
                extract_file = ""

            try:
                result = self.query_execute.execute(server_id, cmd_exec, cmd_params, extract_file)
                self.last_extracted_file = extract_file if file_output else ""
                return result
            except KeyError:
                return False
            except pymssql._pymssql.OperationalError as err:
                err_msg = str("=") * 50 + "\n"
                err_msg += err.args[0][1].decode("utf-8", errors="replace")
                if not err_msg[-1] == "\n":
                    err_msg += "\n"
                err_msg += str("=") * 50
                self._broadcast(err_msg)
                return False
            except psycopg2.OperationalError as err:
                err_msg = str("=") * 50 + "\n"
                err_msg += "".join(err.args[0])
                if not err_msg[-1] == "\n":
                    err_msg += "\n"
                err_msg += str("=") * 50
                self._broadcast(err_msg)
                return False
            except Exception as err:
                self._broadcast(f"Erreur inattendue : {err}")
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

    def get_params_for_debug(self, only_not_parameterized: bool = False) -> dict[int, int]:
        """position et longueur des paramètres après remplacement par leurs valeurs"""
        offset: int = 0
        var_pos: dict[int, int] = {}

        for match in re.finditer(r"(?<![\d\w#_\$@])(@!?[\d\w#_\$@]+)", self.raw_cmd):
            param = match.group(0)
            if param not in self.cmd_params.keys():
                continue
            if only_not_parameterized and not param[0:2] == "@!":
                continue

            value = self.cmd_params.get(param, "")
            if isinstance(value, str) and not param[0:2] == "@!":
                value = "'" + value.replace("'", "''") + "'"
            else:
                value = str(value)

            start = offset + match.start()
            end = offset + match.end() + len(value) - len(param)
            var_pos[start] = end - start
            offset += len(value) - len(param)

        return var_pos

    def _broadcast(self, msg_to_broadcast: str) -> None:
        msg_type: str = "msg_output"
        self.queue.put((msg_type, msg_to_broadcast))
        print(msg_to_broadcast)

    def serialize(self) -> dict:
        display_params: dict[str, str] = {}
        param: _Param
        for key, param in self.params_obj.items():
            display_params[key] = param.display_value

        return {
            "filename": str(self.filename),
            "file_content": self.file_content,
            "cmd_params": self.cmd_params,
            "display_params": display_params,
            "infos": self.infos,
            "raw_cmd": self.raw_cmd,
            "cmd_template": self.cmd_template,
            "name": self.name,
            "hide": self.hide,
            "description": self.description,
            "grp_authorized": self.grp_authorized,
            "servers_id": self.servers_id,
        }

    @classmethod
    def deserialize(cls, data: dict) -> "Query":
        query: Query = cls()
        query.filename = Path(data["filename"])
        query.file_content = data["file_content"]
        query.infos = data["infos"]
        query.raw_cmd = data["raw_cmd"]
        query.cmd_template = data["cmd_template"]
        query.name = data["name"]
        query.hide = data["hide"]
        query.description = data["description"]
        query.grp_authorized = data["grp_authorized"]
        query.servers_id = data["servers_id"]

        query.params_obj = query._init_params()
        for key, val in data["display_params"].items():
            param_obj: _Param = query.params_obj[key]
            param_obj.display_value = val
            query.update_values(key)

        return query


class _Param:
    def __init__(self, sql_declare: str):
        app_settings: settings.Settings = settings.Settings()
        self.converter = Convert(
            date_txt_format=app_settings.date_format,
            field_separator=app_settings.field_separator,
            decimal_separator=app_settings.decimal_separator,
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
        self.ctrl_pattern = ""
        self.ctr_pattern_is_ok = False

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
        comment = re.search(r"--\s*(.+)", self.sql_declare)
        if not comment:
            return

        comment_infos = comment[1].split("|")

        # libellé
        if comment_infos[0]:
            self.description = comment_infos[0]

        # infos optionnel ou UI contrôle
        if len(comment_infos) > 1 and comment_infos[1]:
            infos = re.sub(r"(\(.*?\))|(,)", r"\1^^^", comment_infos[1])
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

        # pattern regex pour ctrl valeur
        if len(comment_infos) > 2 and comment_infos[2]:
            self.ctrl_pattern = ("|").join(comment_infos[2:])

    def _calc_func(self, func: str, func_args: str) -> str:
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
        self.ctr_pattern_is_ok = False
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

        if not val_to_test or not self.ctrl_pattern or re.match(self.ctrl_pattern, val_to_test):
            self.ctr_pattern_is_ok = True

        return self.value_cmd


class _QueryExecute:
    def __init__(self, parent: Query):
        self.converter = Convert()
        self.parent = parent  # pour retourner des messages pour l'interface graphique

        self.cmd_template = ""
        self.cmd_parameters = {}
        self.server: servers.Server = None
        self.extract_file = ""

        self.app_settings: settings.Settings = settings.Settings()
        self.field_separator = self.app_settings.field_separator
        self.print_date_format = PRINT_DATE_FORMAT

    def get_server(self, server_id) -> servers.Server:
        try:
            self.server = servers.Servers().servers_dict[server_id]
        except KeyError:
            error_msg = f"Infos du serveur non trouvé, id : {server_id}"
            self._broadcast(self._time_log() + f" - {error_msg}")
            return None

        return self.server

    def execute(self, server_id, cmd_template, cmd_parameters, extract_file):
        if not self.get_server(server_id):
            return False

        self.cmd_template = cmd_template
        self.cmd_parameters = cmd_parameters
        self.extract_file = extract_file

        if self.cmd_template == "" or self.parent.force_stop.is_set():
            return False

        starting_date = datetime.now()
        self._broadcast(starting_date.strftime(self.print_date_format) + " - Connection à la base de données...")

        self.parent.can_stop.clear()
        with self.server.get_connection() as conn:
            with conn.cursor() as cursor:
                self._broadcast(self._time_log() + " - Requête en cours d'execution...")
                try:
                    cursor.execute(self.cmd_template, self.cmd_parameters)
                    self.parent.can_stop.set()
                except (pymssql._pymssql.ProgrammingError, psycopg2.ProgrammingError) as err:
                    error_code = err.args[0]
                    error_msg = str(err.args[1])[2:-2]
                    self._broadcast(self._time_log() + f" - Erreur {error_code} : {error_msg}")
                    return False
                except Exception as err:
                    self._broadcast(self._time_log() + f" - Erreur d'execution inattendue :\n{', '.join(err.args)}")
                    return False

                self._broadcast(self._time_log() + " - Début récupération des lignes...")
                rows_count, execute_output = self._extract_to_file(cursor)

        ending_date = datetime.now()
        self._execute_end(starting_date, ending_date, rows_count)

        return rows_count, execute_output

    def _execute_end(self, starting_date: datetime, ending_date: datetime, rows_count: int):
        # writing user log
        user_log: logs_user.UserDb = logs_user.UserDb()
        params_for_log = self._params_for_log()
        user_log.insert_exec(
            self.server.id,
            self.parent.name,
            starting_date,
            ending_date,
            rows_count,
            params_for_log,
            self.extract_file,
        )

        # if on, launch central log
        if self.app_settings.logs_are_on:
            user: users.CurrentUser = users.CurrentUser()
            log_infos: dict = {"user_db": user_log.user_db, "user_id": user.username, "user_name": user.title}
            self.parent.queue.put(("central_log", log_infos))

        # notify end result
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

        row_headers = [colname[0] for colname in cursor.description]  # ligne des entêtes de colonnes
        buffer.append(row_headers)  # stockage des entêtes de colonne dans le buffer

        row_number = None
        for row_number, record in enumerate(cursor):  # parcours résultats
            row_content = self._sql_record_to_text(record)
            buffer.append(row_content)
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

            if self.parent.force_stop.is_set():
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

    def _sql_record_to_text(self, record) -> list:
        row_buffer = []

        for j in range(len(record)):  # récup champs et conversion valeur récupérée en texte pour export
            value = record[j]
            value_txt = self.converter.from_result(value)
            try:
                value_txt = value_txt.encode(self.server.charset).decode("utf-8")
            except UnicodeDecodeError:
                pass
            row_buffer.append(value_txt)

        return row_buffer

    def _broadcast(self, msg_to_display: str) -> None:
        self.parent._broadcast(msg_to_display)

    def _file_write(self, rows: list[list[str]]) -> None:
        filename = self.extract_file
        with open(filename, mode="a", encoding="windows-1252", errors="replace", newline="") as f:
            csv_writer = csv.writer(f, delimiter=self.field_separator, quotechar='"', quoting=csv.QUOTE_MINIMAL)
            csv_writer.writerows(rows)

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


class QueryWorker:
    def __init__(self, queue_result: Queue, force_stop: ProcEvent, can_stop: ProcEvent):
        self.process: Process = None
        self.queue_task: Queue = Queue()

        self.queue_result: Queue = queue_result
        self.force_stop: ProcEvent = force_stop
        self.can_stop: ProcEvent = can_stop

        self.creating_worker: bool = False

        self.create_worker()

    def create_worker(self):
        def create():
            args = (self.queue_task, self.queue_result, self.force_stop, self.can_stop)
            self.process = Process(target=self._worker, args=args, daemon=True)
            self.process.start()

            if self.queue_result.get():
                print("Worker ready")

            self.creating_worker = False

        if not self.process or not self.process.is_alive():
            self.creating_worker = True
            print("Worker initializing")
            Thread(target=create, daemon=True).start()

    def kill_and_restart(self):
        print("Task has been asked to stop")

        if self.can_stop.is_set():
            print("Task, stopping normally")
            self.force_stop.set()
            self.process.join()

        if self.process.is_alive():
            print("Task, stopping by terminating process")
            self.process.terminate()  # SIGTERM
            self.process.join(timeout=2.0)

        if self.process.is_alive():
            print("Task, stopping by killing process")
            self.process.kill()  # SIGKILL
            self.process.join()

        print("Task stopped")

        self.create_worker()

    def input_task(self, server_id: str, query_data):
        self.queue_task.put(("start", server_id, query_data))

    def _worker(self, queue_task: Queue, queue_result: Queue, force_stop: ProcEvent, can_stop: ProcEvent):
        original_print = builtins.print  # backup current print function
        builtins.print = (
            lambda *args, **kwargs: None
        )  # disable print, not working in frozen environment for subprocess

        self.queue_task: Queue = queue_task
        self.queue_result: Queue = queue_result
        self.force_stop: ProcEvent = force_stop
        self.can_stop: ProcEvent = can_stop

        self.queue_result.put(True)

        try:
            while True:
                msg_type, self.server_id, self.query_data = self.queue_task.get()
                if msg_type == "stop":
                    break
                elif msg_type == "start":
                    self._task()
        finally:
            builtins.print = original_print  # restore original print function

    def _task(self):
        try:
            self.queue_result.put(("msg_print", "Query task starting"))

            query: Query = Query.deserialize(self.query_data)
            query.queue = self.queue_result
            query.force_stop = self.force_stop
            query.can_stop = self.can_stop

            result = query.execute_cmd(True, self.server_id)
            rows_number, output_file = result if isinstance(result, tuple) else (0, "")
            self.queue_result.put(("result", (rows_number, output_file)))

            self.queue_result.put(("msg_print", "Query task ending"))
        except Exception as e:
            self.queue_result.put(("error", str(e)))
        finally:
            self.queue_result.put(("done", None))


def get_queries(folder: Path) -> tuple[list[Query], list[str]]:
    if not Path(folder).is_dir():
        raise ValueError(f"Erreur : le répertoire {folder} n'a pas été trouvé ou n'est pas accessible !")

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
    server: servers.Server = all_servers.servers_dict.get(server_id, None)
    if not user.admin and not (server.grp_authorized == [] or set(user.grp_authorized) & set(server.grp_authorized)):
        return filtered

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
    print(my_query.get_infos_for_exec())
    print(my_query.get_cmd_for_debug())
    print(my_query.cmd_params)
    my_query.execute_cmd(file_output=True)

    orphan_queries(settings.Settings().queries_folder)
