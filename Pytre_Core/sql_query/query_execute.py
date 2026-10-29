import csv
from datetime import datetime
from pathlib import Path
from multiprocessing import Queue, Event as proc_get_event
from multiprocessing.synchronize import Event as ProcEvent
from typing import Any

from Pytre_Core import users, logs_user
from Pytre_Core.context import AppContext, UserContext
from Pytre_Core.remotes import RemoteCursor, RemoteError, RemoteRequestError, RemoteServer
from Pytre_Core.servers import Server
from Pytre_Core.sql_query.execute_config import LocalExecuteCfg, RemoteExecuteCfg
from Pytre_Core.sql_query.execution_msg import ExecResultMsg
from Pytre_Core.sql_query.converter import SqlTypeConverter
from Pytre_Core.sql_query.log_utils import _STANDALONE_BROADCAST_LOGGER
from Pytre_Core.sql_query.query import Query
from Pytre_Core.sql_query.query_drivers_client import ServerConnectionError, LocalCursor, DriverErrors, get_connection


Cursor = RemoteCursor | LocalCursor

PRINT_DATE_FORMAT: str = "%d/%m/%Y à %H:%M:%S"  # date format for logs and output


class QueryExecute:
    def __init__(self, app_ctx: AppContext, user_ctx: UserContext, query: Query | None = None):
        self.app_ctx: AppContext = app_ctx
        self.user_ctx: UserContext = user_ctx
        self.query: Query | None = query

        display_format = query.display_format if query else user_ctx.display_format
        self.converter: SqlTypeConverter = SqlTypeConverter(
            date_display=display_format.date_format,
            decimal_separator=display_format.decimal_separator,
        )

        self.config: LocalExecuteCfg | RemoteExecuteCfg | None = None
        self.server: Server | None = None
        self.remote: RemoteServer | None = None

        self.print_date_format = PRINT_DATE_FORMAT

        self.queue: Queue = Queue()  # to relay message to UI
        self.stop_requested: ProcEvent = proc_get_event()  # to signal that execution must be stopped
        self.cannot_stop: ProcEvent = proc_get_event()  # to know if execution cannot be stopped cleanly

    def _load_exec_config(self, server_id: str, file_output: bool) -> None:
        """Load execution config from query"""
        if self.query is None:
            raise ValueError("A query is required to load a config grom it")

        config = self.query.get_exec_config(server_id, file_output)

        if isinstance(config, RemoteExecuteCfg):
            self.remote = config.remote_server
        else:
            self._get_server(config.server_id)

        self.config = config

    def _get_server(self, server_id: str) -> Server | None:
        """Gets, sets and returns a local server from its `server_id`"""
        try:
            self.server = self.app_ctx.servers.servers_dict[server_id]
        except KeyError:
            error_msg = f"Infos du serveur non trouvé, id : {server_id}"
            self._broadcast(self._time_log() + f" - {error_msg}")
            return None

        return self.server

    def _check_exec_config(self) -> None:
        """Validates that everything is set correctly to run a query.
        Raises a FileExistsError or ValueError if something is wrong."""
        # validate that a config exists
        if self.config is None:
            raise ValueError("Config d'execution manquante")

        # validate the extraction file
        if self.config.extract_file != Path() and self.config.extract_file.exists():
            raise FileExistsError(f"Le fichier {self.config.extract_file} existe déjà.")

        # validation checks for a RemoteServer
        if isinstance(self.config, RemoteExecuteCfg):
            if self.remote is None:
                raise ValueError("Config d'execution remote mais pas de serveur remote initialisée.")
            if self.config.template_hash == "":
                raise ValueError("Config d'execution remote incomplète : template_hash manquant.")
        # validation checks for a LocalServer
        else:
            if self.server is None:
                raise ValueError("Config d'execution local mais pas de serveur initialisé.")
            if self.config.cmd_template == "":
                raise ValueError("Config d'execution local incomplète : cmd_template manquant.")

    def _broadcast_operational_error(self, err: ServerConnectionError):
        """Broadcasts an operational error"""
        err_msg = str("=") * 50 + "\n" + str(err)
        if not err_msg[-1] == "\n":
            err_msg += "\n"
        err_msg += str("=") * 50
        self._broadcast(err_msg)

    # ------------------------------------------------------------------------------------------
    # Execution functions : executions are dispatched to _execute_local / _execute_remote
    # then both converge on _extract_records then _execute_log_results() on successful extraction
    # ------------------------------------------------------------------------------------------
    def execute(self, server_id: str = "", file_output: bool = True) -> tuple[int, Path | list[list[str]] | None]:
        """Executes a Query locally or via a remote Pytre_Server.
        Returns as a tuple how many lines have been extracted and, depending on `file_output`,
        a list of records if false or a filepath if True.
        Raises ValueError if no query is set, any other error returns (-1, None) instead."""

        if self.query is None:
            raise ValueError("A query is required to execute it, `self.query` must be set")

        # Update query parameters to current values
        self.query.update_values()
        if not self.query.values_ok():
            err_msg = "\nImpossible d'executer des paramètres sont invalides"
            self._broadcast(err_msg)
            return (-1, None)

        # Load and check execution config
        try:
            self._load_exec_config(server_id, file_output)
            self._check_exec_config()
        except Exception as e:
            err_msg = f"\nErreur chargement config d'execution : {e}"
            self._broadcast(err_msg)
            return (-1, None)

        # Start query execution
        try:
            start_date = datetime.now()
            result = self._execute_dispatch(start_date)
            return result
        except ServerConnectionError as err:
            self._broadcast_operational_error(err)
            return (-1, None)
        except Exception as err:
            self._broadcast(f"Erreur inattendue : {err}")
            return (-1, None)

    def _execute_dispatch(self, start_date: datetime) -> tuple[int, Path | list[list[str]] | None]:
        """Dispatch execution to local or via a remote Pytre_Server.
        Returns early if a stop was already requested."""

        if self.stop_requested.is_set():
            return (-1, None)
        elif isinstance(self.config, RemoteExecuteCfg):
            return self._execute_remote(start_date)
        else:
            return self._execute_local(start_date)

    def _execute_local(self, start_date: datetime) -> tuple[int, Path | list[list[str]] | None]:
        """Executes a Query locally.
        Returns as a tuple how many lines have been extracted and, depending on config.extract_file,
        a list of records or a filepath. Errors return (-1, None)"""

        if self.server is None or self.config is None:
            raise RuntimeError("Server and ExecuteConfig must be initialized to be able to execute")

        assert isinstance(self.config, LocalExecuteCfg)
        cfg: LocalExecuteCfg = self.config

        self._broadcast(start_date.strftime(self.print_date_format) + " - Connexion à la base de données...")
        self.cannot_stop.set()
        with get_connection(self.server) as conn, conn.cursor() as cursor:
            self._broadcast(self._time_log() + " - Requête en cours d'execution...")
            self.queue.put((ExecResultMsg.START_TIMER, ""))
            try:
                # execute query
                cursor.execute(cfg.cmd_template, cfg.cmd_parameters)
                self.cannot_stop.clear()
                self.queue.put((ExecResultMsg.STOP_TIMER, ""))
                # extract records
                return self._extract_records(cursor, start_date)
            except DriverErrors.ProgrammingError as err:
                self._broadcast(self._time_log() + f" - Erreur SQL : {str(err)}")
                return (-1, None)
            except DriverErrors.Error as err:
                error_type = type(err).__name__
                self._broadcast(self._time_log() + f" - Erreur d'execution inattendue ({error_type}) :\n{str(err)}")
                return (-1, None)

    def _execute_remote(self, start_date: datetime) -> tuple[int, Path | list[list[str]] | None]:
        """Executes a Query via a remote Pytre_Server.
        Returns as a tuple how many lines have been extracted and, depending on config.extract_file,
        a list of records or a filepath. Errors return (-1, None)"""

        if self.remote is None or self.config is None:
            raise RuntimeError("RemoteServer and ExecuteConfig must be initialized to be able to execute")

        assert isinstance(self.config, RemoteExecuteCfg)
        cfg: RemoteExecuteCfg = self.config

        self._broadcast(start_date.strftime(self.print_date_format) + " - Connexion à la remote...")
        self.cannot_stop.set()
        self._broadcast(self._time_log() + " - Requête en cours d'execution...")
        self.queue.put((ExecResultMsg.START_TIMER, ""))
        try:
            # execute query
            with self.remote.execute(
                cfg.remote_query_id, cfg.template_hash, cfg.display_format, cfg.display_parameters
            ) as cursor:
                self.cannot_stop.clear()
                self.queue.put((ExecResultMsg.STOP_TIMER, ""))
                # extract records
                return self._extract_records(cursor, start_date)
        except RemoteRequestError as e:
            self._broadcast(self._time_log() + f" - Remote injoignable : {e}")
            return (-1, None)
        except RemoteError as e:
            self._broadcast(self._time_log() + f" - Erreur du serveur remote : {e}")
            return (-1, None)

    def _execute_log_results(self, starting_date: datetime, ending_date: datetime, rows_count: int):
        """Writes the user/central logs and broadcasts a completion summary."""

        source = self.server or self.remote

        assert source is not None and self.config is not None

        # writing user log
        user_log: logs_user.UserLogs = self.user_ctx.user_logs
        user_log.insert_exec(
            source.id,
            self.config.query_id,
            starting_date,
            ending_date,
            rows_count,
            self.config.params_metadata,
            self.config.extract_file,
        )

        # if on, launch central log
        if not self.remote and self.app_ctx.settings.logs_are_on:
            user: users.User = self.user_ctx.user
            log_infos: dict = {"user_logs": user_log.db_path, "user_id": user.username, "user_name": user.title}
            self.queue.put((ExecResultMsg.CENTRAL_LOG, log_infos))

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

    # ------------------------------------------------------------------------------------------
    # Extraction functions : _extract_records dispatches to _extract_to_list / _extract_to_file
    # which convert each row via _convert_sql_record
    # ------------------------------------------------------------------------------------------
    def _extract_records(self, cursor: Cursor, start_date: datetime) -> tuple[int, Path | list[list[str]] | None]:
        """Extracts to a list or file once the cursor source is ready - shared by local and remote execution.
        Returns as a tuple how many lines have been extracted and, depending on config.extract_file,
        a list of records or a filepath. Errors return (-1, None)"""

        assert self.config is not None

        # if no extraction file then return rows in a list
        if self.config.extract_file == Path():
            rows_count, execute_output = self._extract_to_list(cursor)
            return rows_count, execute_output

        # else extract rows to a file
        try:
            self._broadcast(self._time_log() + " - Début récupération des lignes...")
            rows_count, execute_output = self._extract_to_file(cursor)
            if execute_output == "" and self.stop_requested.is_set():
                raise InterruptedError()
            ending_date = datetime.now()
            self._execute_log_results(start_date, ending_date, rows_count)
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

        # if complete write failed then remove partial file
        if self.config.extract_file != Path():
            self.config.extract_file.unlink(missing_ok=True)

        return (-1, None)

    def _extract_to_list(self, cursor: Cursor) -> tuple[int, list[list[str]]]:
        """Extracts records from a local or remote cursor into a list.
        Returns as a tuple how many lines have been extracted and a list of records."""

        buffer: list[list[str]] = []
        row_num = -1

        # add header to buffer
        if cursor.description:
            buffer.append([colname[0] for colname in cursor.description])

        for row_num, record in enumerate(cursor):
            # return if stop requested
            if self.stop_requested.is_set():
                return 0, []

            # add record to buffer
            buffer.append(self._convert_sql_record(record))

        return row_num + 1, buffer

    def _extract_to_file(self, cursor: Cursor) -> tuple[int, Path | None]:
        """Extracts records from a local or remote cursor into a file.
        Returns as a tuple how many lines have been extracted and a filepath."""

        buffer = []  # buffer to hold rows before writing to file
        buffer_max_size = 50_000  # number of lines to trigger file writing
        row_num = -1

        if self.config is None or self.config.extract_file == Path():
            raise ValueError("Extraction to file is not possible without specifying one")

        # get csv format
        encoding = self.config.csv_format.encoding
        delimiter = self.config.csv_format.delimiter
        quotechar = self.config.csv_format.quotechar

        # write csv
        self.config.extract_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config.extract_file, mode="w", encoding=encoding, errors="replace", newline="") as f:
            csv_writer = csv.writer(f, delimiter=delimiter, quotechar=quotechar, quoting=csv.QUOTE_MINIMAL)

            # add header to buffer
            if cursor.description:
                buffer.append([colname[0] for colname in cursor.description])

            for row_num, record in enumerate(cursor):
                # return if stop requested
                if self.stop_requested.is_set():
                    return 0, None

                # add record to buffer
                buffer.append(self._convert_sql_record(record))

                # flush buffer if full
                if (row_num + 1) % buffer_max_size == 0:
                    self._broadcast(
                        self._time_log() + " - Ecriture ligne : {:,}...".format(row_num + 1).replace(",", " ")
                    )
                    csv_writer.writerows(buffer)
                    buffer.clear()

            # write remaining buffer if not empty and not only the header
            if buffer and row_num != -1:
                self._broadcast(self._time_log() + " - Ecriture des dernières lignes...")
                csv_writer.writerows(buffer)
                buffer.clear()

        self._broadcast(self._time_log() + " - Ecriture finie")

        if row_num == -1:
            self.config.extract_file.unlink(missing_ok=True)

        return row_num + 1, self.config.extract_file

    def _convert_sql_record(self, record: tuple[Any, ...] | None) -> list:
        """Converts record's values to display text"""

        charset = self.server.charset if self.server is not None else None  # a remote already returns clean encoding
        convert_func = self.converter.from_result

        def clean_value(val):
            value_txt = convert_func(val)

            if charset is not None and isinstance(val, str):
                try:
                    return value_txt.encode(charset).decode("utf-8")
                except (UnicodeDecodeError, UnicodeEncodeError, LookupError):
                    return value_txt

            return value_txt

        return [clean_value(val) for val in record] if record else []

    # ------------------------------------------------------------------------------------------
    # Log functions
    # ------------------------------------------------------------------------------------------
    def _broadcast(self, msg_to_display: str, msg_type: ExecResultMsg = ExecResultMsg.UI_OUTPUT) -> None:
        self.queue.put((msg_type, msg_to_display))
        _STANDALONE_BROADCAST_LOGGER.info(msg_to_display)

    def _time_log(self) -> str:
        return datetime.now().strftime(self.print_date_format)
