import csv
import codecs
from datetime import datetime
from pathlib import Path
from multiprocessing import Queue, Event as proc_get_event
from multiprocessing.synchronize import Event as ProcEvent

import pymssql
import psycopg2

from Pytre import settings, users, servers, logs_user
from Pytre.app_context import AppContext
from Pytre.sql_query.execute_config import ExecuteConfig
from Pytre.sql_query.execution_msg import ExecResultMsg
from Pytre.sql_query.converter import SqlTypeConverter
from Pytre.sql_query.log_utils import _STANDALONE_BROADCAST_LOGGER

PRINT_DATE_FORMAT: str = "%d/%m/%Y à %H:%M:%S"  # pour le format de la date pour les logs / output


class QueryExecute:
    def __init__(self, ctx: AppContext):
        self.ctx: AppContext = ctx

        self.converter: SqlTypeConverter = SqlTypeConverter()

        self.config: ExecuteConfig | None = None
        self.server: servers.Server | None = None

        self.app_settings: settings.Settings = ctx.settings
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
            self.server = self.ctx.servers.servers_dict[server_id]
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
            self.queue.put((ExecResultMsg.START_TIMER, ""))
            try:
                cursor.execute(self.config.cmd_template, self.config.cmd_parameters)
                self.cannot_stop.clear()
                self.queue.put((ExecResultMsg.STOP_TIMER, ""))
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
        user_log: logs_user.UserDb = self.ctx.user_db
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
            user: users.CurrentUser = self.ctx.current_user
            log_infos: dict = {"user_db": user_log.user_db, "user_id": user.username, "user_name": user.title}
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

    def _broadcast(self, msg_to_display: str, msg_type: ExecResultMsg = ExecResultMsg.UI_OUTPUT) -> None:
        self.queue.put((msg_type, msg_to_display))
        _STANDALONE_BROADCAST_LOGGER.info(msg_to_display)

    def _time_log(self) -> str:
        return datetime.now().strftime(self.print_date_format)
