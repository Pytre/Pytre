from enum import StrEnum


class ExecTaskMsg(StrEnum):
    """Messages from UI to execution worker process"""

    START = "start"
    STOP = "stop"


class ExecResultMsg(StrEnum):
    """Messages from execution worker process to UI"""

    PRINT = "msg_print"
    UI_OUTPUT = "msg_output"
    LOG = "log"
    START_TIMER = "start_timer"
    STOP_TIMER = "stop_timer"
    RESULT = "result"
    ERROR = "error"
    CENTRAL_LOG = "central_log"
    DONE = "done"
