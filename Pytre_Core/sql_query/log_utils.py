import logging
from multiprocessing import Queue

from Pytre_Core import utils
from Pytre_Core.sql_query.execution_msg import ExecResultMsg

# Global variables used for logging
_LOG_QUEUE: Queue | None = None
# Logger to allow console output when module is directly launched
# otherwise do nothing since the root log level not set to INFO by setup_standalone_logging
_STANDALONE_BROADCAST_LOGGER: logging.Logger = logging.getLogger("sql_query_debug")


def set_log_queue(queue: Queue | None) -> None:
    """
    Route log() through the queue used by Query/QueryExecute for exec msg, instead of logging locally.
    Pass None to revert to local logging.
    """
    global _LOG_QUEUE
    _LOG_QUEUE = queue


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    if _LOG_QUEUE is None:
        utils.log(msg, level=level, sublogger=sublogger)
    else:
        _LOG_QUEUE.put((ExecResultMsg.LOG, (msg, level, sublogger)))
