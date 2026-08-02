import logging
from threading import Thread, Lock
from queue import Empty as QueueIsEmpty
from multiprocessing import Process, Queue, Event as proc_get_event
from multiprocessing.synchronize import Event as ProcEvent
from dataclasses import dataclass

from Pytre.app_context import AppContext, build_app_context
from Pytre.sql_query.execution_msg import ExecTaskMsg, ExecResultMsg
from Pytre.sql_query.query import Query
from Pytre.sql_query.log_utils import set_log_queue, log as _log


def log(msg: str | object, level: int = logging.INFO, sublogger: str = __name__) -> None:
    _log(msg, level=level, sublogger=sublogger)


@dataclass(frozen=True)
class ProcessConfig:
    queue_task: Queue
    queue_result: Queue
    stop_requested: ProcEvent
    cannot_stop: ProcEvent


class ExecutionRunner:
    def __init__(self):
        self.process: Process | None = None
        self.queue_task: Queue = Queue()
        self.queue_result: Queue = Queue()
        self.stop_requested: ProcEvent = proc_get_event()
        self.cannot_stop: ProcEvent = proc_get_event()

        self._create_lock: Lock = Lock()

        self.creating_process: bool = False
        self.process_ready: bool = False
        self.task_killed: bool = False

        self.create_process()

    def create_process(self):
        def create():
            config = ProcessConfig(
                queue_task=self.queue_task,
                queue_result=self.queue_result,
                stop_requested=self.stop_requested,
                cannot_stop=self.cannot_stop,
            )

            self.process = Process(target=run_execution_process, args=(config,), daemon=True)
            self.process.start()

            try:
                if self.queue_result.get(timeout=90.0):  # long timeout : allow slow init (network, antivirus, ...)
                    with self._create_lock:
                        self.process_ready = True
                    log("Execution process ready")
                else:
                    log("Execution process aborted : database access unavailable", logging.ERROR)
            except QueueIsEmpty:
                log("Execution process failed to initialize (timeout)", logging.WARNING)
                if self.process and self.process.is_alive():
                    log("Terminating stuck execution process...")
                    self.process.terminate()
                    self.process.join(timeout=2.0)
                    if self.process.is_alive():
                        self.process.kill()
            finally:
                with self._create_lock:
                    self.creating_process = False

        with self._create_lock:
            if self.creating_process:
                log("Execution process already being created")
                return
            elif self.process and self.process.is_alive():
                log("Execution process already exists and is alive")
                return

            self.process_ready = False
            self.creating_process = True

        log("Execution process initializing")
        Thread(target=create, daemon=True).start()

    def kill_and_restart(self):
        log("Task has been asked to stop")

        with self._create_lock:
            self.process_ready = False

        if not self.process:
            log("Task, stopping error : execution process does not exist", logging.WARNING)
        else:
            if not self.cannot_stop.is_set():
                log("Trying to stop task normally")
                self.stop_process()
                self.process.join(timeout=5.0)

            if self.process.is_alive():
                log("Trying to stop task by terminating execution process")
                self.process.terminate()  # SIGTERM
                self.process.join(timeout=2.0)

            if self.process.is_alive():
                log("Trying to stop task by killing execution process")
                self.process.kill()  # SIGKILL
                self.process.join()

            log("Task stopped")

        # flag task as killed
        with self._create_lock:
            self.task_killed = True

        # reset events and queues
        self.cannot_stop.clear()
        self.stop_requested.clear()
        for queue in (self.queue_result, self.queue_task):
            try:
                while True:
                    queue.get_nowait()
            except QueueIsEmpty:
                pass

        # recreate process
        self.create_process()

    def input_task(self, server_id: str, query_data):
        with self._create_lock:
            if self.creating_process:
                raise RuntimeError("Can't run query task execution process being created...")
            if not self.process_ready:
                raise RuntimeError("Can't run query task execution process not ready...")

        if not self.process or not self.process.is_alive():
            self.create_process()
            raise RuntimeError("Can't execute query, execution process not alive, recreating...")

        with self._create_lock:
            self.task_killed = False

        self.cannot_stop.clear()
        self.stop_requested.clear()
        self.queue_task.put((ExecTaskMsg.START, server_id, query_data))

    def stop_process(self):
        self.stop_requested.set()
        self.queue_task.put((ExecTaskMsg.STOP, "", ""))


class ExecutionProcess:
    def __init__(self, config: ProcessConfig):
        set_log_queue(config.queue_result)
        self.queue_task: Queue = config.queue_task
        self.queue_result: Queue = config.queue_result
        self.stop_requested: ProcEvent = config.stop_requested
        self.cannot_stop: ProcEvent = config.cannot_stop
        self.ctx: AppContext = build_app_context()

    def start(self):
        if self.ctx.kee.is_ko:
            log("Execution process aborting : database access to settings is broken", logging.ERROR)
            try:
                self.queue_result.put(False)
            except Exception as e:
                log(f"Execution process failed to signal not-ready state : {e}", logging.ERROR)
            return

        try:
            self.queue_result.put(True)
        except Exception as e:
            log(f"Execution process failed to signal ready state : {e}", logging.ERROR)
            return

        try:
            while True:
                msg_type, server_id, query_data = self.queue_task.get()
                if msg_type == ExecTaskMsg.STOP:
                    break
                elif msg_type == ExecTaskMsg.START:
                    self.run_task(server_id, query_data)
        except Exception as e:
            log(f"Execution process unexpected error : {e}", logging.ERROR)
        finally:
            log("Execution process ending")

    def run_task(self, server_id: str, query_data: dict):
        try:
            self.queue_result.put((ExecResultMsg.PRINT, "Query task starting"))

            query: Query = Query.deserialize(self.ctx, query_data)
            query.query_execute.queue = self.queue_result
            query.query_execute.stop_requested = self.stop_requested
            query.query_execute.cannot_stop = self.cannot_stop

            result = query.execute(server_id=server_id, file_output=True)
            rows_number, output_file = result
            del query
            self.queue_result.put((ExecResultMsg.RESULT, (rows_number, output_file)))

            self.queue_result.put((ExecResultMsg.PRINT, "Query task ending"))
        except Exception as e:
            self.queue_result.put((ExecResultMsg.ERROR, str(e)))
        finally:
            self.queue_result.put((ExecResultMsg.DONE, None))


def run_execution_process(config: ProcessConfig):
    exec_process = ExecutionProcess(config)
    exec_process.start()
