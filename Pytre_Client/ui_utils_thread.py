import logging
import tkinter as tk
from queue import Queue, Empty as QueueIsEmpty

from Pytre_Core.utils import log as _log


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


def tk_call_when_ready(tk_window: tk.Toplevel, result_queue: Queue, callback, check_interval_ms: int = 100):
    """
    Execute callback from main thread using results from a queue as arguments.

    Args:
        result_queue: queue to retrieve arguments, can be args or an (args, kwargs) tuple
        callback : function to execute with the results
        check_interval_ms: interval to check the queue in ms
    """

    def check():
        try:
            result = result_queue.get_nowait()

            # if not a tuple:
            if not isinstance(result, tuple):
                callback(result)
            # if (args, kwargs) tuple
            elif len(result) == 2 and isinstance(result[1], dict):
                args, kwargs = result
                callback(*args, **kwargs)
            # else when (arg1, arg2, ...) tuple
            else:
                callback(*result)
        except QueueIsEmpty:
            tk_window.after(check_interval_ms, check)
        except Exception as e:
            log(f"Error while checking queue: {e}", logging.ERROR)
            tk_window.after(check_interval_ms, check)

    # start queue checking
    tk_window.after(check_interval_ms, check)
