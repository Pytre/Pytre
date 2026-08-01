import logging
from pathlib import Path

from Pytre import utils
from Pytre.app_context import build_app_context
from Pytre.sql_query.query import Query
from Pytre.sql_query.loader import orphan_queries
from Pytre.sql_query.log_utils import log as _log


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


if __name__ == "__main__":
    utils.setup_standalone_logging()

    ctx = build_app_context()
    APP_PATH = ctx.settings.app_path

    # queries, errors = get_queries(Path(app_settings.queries_folder))

    sql_script = list(Path(ctx.settings.queries_folder).glob("*.sql"))[0]
    sql_script = Path(ctx.settings.queries_folder) / "ZCOMP.sql"

    my_query = Query(ctx, sql_script)
    my_query.update_values()
    log(my_query.get_exec_config())
    log(my_query.get_cmd_for_debug())
    my_query.execute(file_output=True)

    orphan_queries(ctx, ctx.settings.queries_folder)
