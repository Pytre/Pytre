import logging
from pathlib import Path

from Pytre_Core.context import AppContext, UserContext
from Pytre_Core.remotes import RemoteServer
from Pytre_Core.servers import Servers, Server
from Pytre_Core.users import User
from Pytre_Core.sql_query.file_parser import FileParser
from Pytre_Core.sql_query.query import Query
from Pytre_Core.sql_query.log_utils import log as _log


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


def load_files(folder: Path) -> tuple[list[FileParser], list[str]]:
    """Parses every .sql file in `folder` into a FileParser.
    Files failing to parse are ignored and their error are collected instead."""

    if not Path(folder).is_dir():
        raise ValueError(f"Erreur : le répertoire {Path(folder)} n'a pas été trouvé ou n'est pas accessible !")

    parsers: list[FileParser] = []
    errors: list[str] = []

    files = list(Path(folder).glob("*.sql"))
    for file in files:
        try:
            parser = FileParser(Path(file))
        except ValueError as e:
            error = f"Erreur chrgmt '{Path(file).name}' : ValueError, {e}"
            errors.append(error)
            log(error, level=logging.WARNING)
            continue
        except Exception as e:
            error = f"Erreur chrgmt '{Path(file).name}' : {e.__class__.__name__}, {e}"
            errors.append(error)
            log(error, level=logging.WARNING)
            continue  # si erreur, ne pas bloquer et ignorer la requête

        parsers.append(parser)

    return parsers, errors


def build_queries_from_parsers(
    app_ctx: AppContext, user_ctx: UserContext, parsers: list[FileParser]
) -> tuple[list[Query], list[str]]:
    """Builds a Query per parser, for user.
    Parsers that fail to build are skipped and their error are collected instead."""

    queries: list[Query] = []
    errors: list[str] = []

    for parser in parsers:
        try:
            my_query = Query(app_ctx, user_ctx, parser)
        except ValueError as e:
            error = f"Erreur création Query pour '{parser.filename.name}' : ValueError, {e}"
            errors.append(error)
            log(error, level=logging.WARNING)
            continue
        except Exception as e:
            error = f"Erreur création Query pour '{parser.filename.name}' : {e.__class__.__name__}, {e}"
            errors.append(error)
            log(error, level=logging.WARNING)
            continue  # si erreur, ne pas bloquer et ignorer la requête

        queries.append(my_query)

    queries.sort(key=lambda k: k.name)

    return queries, errors


def build_queries_from_remote(
    app_ctx: AppContext, user_ctx: UserContext, remote_server: RemoteServer
) -> tuple[list[Query], list[str]]:
    """Builds a Query per entry in remote_server.serialized_queries, for user.
    Entries that fail to deserialize are skipped and their error are collected instead."""

    queries: list[Query] = []
    errors: list[str] = []

    for data in remote_server.serialized_queries:
        try:
            my_query = Query.deserialize(app_ctx, user_ctx, data, remote=remote_server)
        except (KeyError, ValueError) as e:
            error = f"Erreur création Query remote pour '{remote_server.id_on_remote}' : {e.__class__.__name__}, {e}"
            errors.append(error)
            log(error, level=logging.WARNING)
            continue  # si erreur, ne pas bloquer et ignorer la requête

        queries.append(my_query)

    queries.sort(key=lambda k: k.name)

    return queries, errors


def get_queries(app_ctx: AppContext, user_ctx: UserContext, folder: Path) -> tuple[list[Query], list[str]]:
    """Loads and builds every local Query from `folder` in one call.
    Errors are not fatal and are collected instead."""

    parsers, load_errors = load_files(folder)
    queries, build_errors = build_queries_from_parsers(app_ctx, user_ctx, parsers)
    return queries, load_errors + build_errors


def filter_queries(servers: Servers, queries: list[Query], server_id: str, user: User) -> list[Query]:
    """Returns the queries from `queries` that user may run on server_id:
    authorized for the server, authorized for the query, and query not hidden from them."""

    filtered: list[Query] = []

    # check if user is authorized for server
    all_servers: Servers = servers
    server: Server | None = all_servers.servers_dict.get(server_id, None)
    if server is None:
        return []
    if not user.admin:
        if server.grp_authorized != [] and not set(user.groups) & set(server.grp_authorized):
            return []

    # check which query user can see
    for query in queries:
        # ignore query if it's not for server_id
        if server_id not in query.servers_id:
            continue

        # ignore query if user not allowed to access it
        if not user.admin and not query.grp_authorized == [] and not set(user.groups) & set(query.grp_authorized):
            continue

        # finally check hidden property to determine if query must be listed
        if query.hide == 0 or (user.admin and query.hide != 2):
            filtered.append(query)

    return filtered


def orphan_queries(app_ctx: AppContext, user_ctx: UserContext, folder: Path) -> list[Path]:
    """Returns .sql files in `folder` with declared servers no longer existing in app_ctx"""

    if not Path(folder).is_dir():
        raise ValueError(f"Erreur : le répertoire {folder} n'a pas été trouvé ou n'est pas accessible !")

    orphan_files: list[Path] = []

    all_ids = app_ctx.servers.servers_dict.keys()

    files = list(Path(folder).glob("*.sql"))
    for file in files:
        try:
            query = Query(app_ctx, user_ctx, Path(file))
            if not set(query.servers_id) & set(all_ids):
                orphan_files.append(Path(file))
                log(f"Aucun serveur existant pour : {Path(file).name}", logging.WARNING)
        except Exception as e:
            error = f"Erreur chrgmt '{Path(file).name}' : {e.__class__.__name__}, {e}"
            log(error, logging.WARNING)
            continue  # si erreur, ne pas bloquer et ignorer la requête

    return orphan_files
