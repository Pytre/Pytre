import logging
from pathlib import Path

from Pytre import servers, users
from Pytre.context import AppContext, UserContext
from Pytre.sql_query.query import Query
from Pytre.sql_query.log_utils import log as _log


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


def get_queries(app_ctx: AppContext, user_ctx: UserContext, folder: Path) -> tuple[list[Query], list[str]]:
    if not Path(folder).is_dir():
        raise ValueError(f"Erreur : le répertoire {Path(folder)} n'a pas été trouvé ou n'est pas accessible !")

    queries: list[Query] = []
    errors: list[str] = []

    files = list(Path(folder).glob("*.sql"))
    for file in files:
        try:
            my_query = Query(app_ctx, user_ctx, Path(file))
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

        queries.append(my_query)

    queries.sort(key=lambda k: k.name)

    return queries, errors


def filter_queries(servers: servers.Servers, queries: list[Query], server_id: str, user: users.User) -> list[Query]:
    filtered: list[Query] = []

    # check if user is authorized for server
    all_servers: servers.Servers = servers
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


def orphan_queries(app_ctx: AppContext, user_ctx: UserContext, folder: Path) -> list[Path]:
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
