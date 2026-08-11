import logging
from pathlib import Path

from Pytre_Core import utils
from Pytre_Core.context import AppContext, UserContext
from Pytre_Core.sql_query.query import Query
from Pytre_Core.sql_query.loader import orphan_queries
from Pytre_Core.sql_query.log_utils import log as _log


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


if __name__ == "__main__":
    utils.setup_standalone_logging()

    app_ctx = AppContext()
    user_ctx = UserContext(app_ctx.users)

    APP_PATH = app_ctx.settings.app_path

    # queries, errors = get_queries(Path(app_settings.queries_folder))

    sql_script = list(Path(app_ctx.settings.queries_folder).glob("*.sql"))[0]
    sql_script = Path(app_ctx.settings.queries_folder) / "ZCOMP.sql"

    my_query = Query(app_ctx, user_ctx, sql_script)
    my_query.update_values()
    log(my_query.get_exec_config())
    log(my_query.get_cmd_for_debug())
    my_query.execute(file_output=True)

    orphan_queries(app_ctx, user_ctx, app_ctx.settings.queries_folder)
