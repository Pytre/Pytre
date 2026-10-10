"""
Tests for Pytre/sql_query/execution_runner.py

Basic tests for SQL execution runner infrastructure.
Full integration tests skipped due to multiprocessing complexity.
"""

import pytest
from typing import cast
from unittest.mock import Mock, patch, MagicMock
from multiprocessing import Queue, Event as proc_get_event
from queue import Empty as QueueIsEmpty

from Pytre_Core.context import AppMode
from Pytre_Core.sql_query.execution_runner import ExecutionRunner, ProcessConfig


# ================================================================================
# ProcessConfig Tests
# ================================================================================


@pytest.mark.unit
def test_process_config_creation():
    """ProcessConfig should be a frozen dataclass"""
    queue_task = Queue()
    queue_result = Queue()
    stop_requested = proc_get_event()
    cannot_stop = proc_get_event()

    config = ProcessConfig(
        queue_task=queue_task,
        queue_result=queue_result,
        stop_requested=stop_requested,
        cannot_stop=cannot_stop,
    )

    assert config.queue_task == queue_task
    assert config.queue_result == queue_result
    assert config.stop_requested == stop_requested
    assert config.cannot_stop == cannot_stop


@pytest.mark.unit
def test_process_config_frozen():
    """ProcessConfig should be immutable (frozen)"""
    config = ProcessConfig(
        queue_task=Queue(),
        queue_result=Queue(),
        stop_requested=proc_get_event(),
        cannot_stop=proc_get_event(),
    )

    # Should not be able to modify
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        config.queue_task = Queue()  # type: ignore


# ================================================================================
# ExecutionRunner Initialization Tests
# ================================================================================


def _wait_until(predicate, timeout=2.0, interval=0.01):
    """Poll predicate() until it's truthy or the timeout elapses; assert it settled."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(interval)
    assert predicate(), "condition never became true within timeout"


class _FakeProcessFactory:
    """
    Build a Process replacement whose start() immediately signals readiness on
    queue_result, simulating a child process that initializes instantly - avoids
    spawning a real OS process and the real 90s init timeout.
    """

    def __init__(self):
        self.created: list[Mock] = []

    def __call__(self, target=None, args: tuple = (), daemon=None):
        config = args[0]
        mock_proc = Mock()
        mock_proc.is_alive = Mock(return_value=True)

        def fake_start():
            config.queue_result.put(True)

        mock_proc.start = fake_start
        self.created.append(mock_proc)
        return mock_proc


@pytest.fixture
def fake_process_factory():
    return _FakeProcessFactory()


@pytest.mark.unit
def test_execution_runner_initialization(fake_process_factory):
    """ExecutionRunner should initialize with queues and events, and become ready"""
    with patch("Pytre_Core.sql_query.execution_runner.Process", side_effect=fake_process_factory):
        runner = ExecutionRunner(AppMode.HYBRID)

        assert runner.queue_task is not None
        assert runner.queue_result is not None
        assert runner.stop_requested is not None
        assert runner.cannot_stop is not None
        assert runner.task_killed is False

        _wait_until(lambda: runner.process_ready is True)
        _wait_until(lambda: runner.creating_process is False)


@pytest.mark.unit
def test_execution_runner_stays_not_ready_if_child_signals_failure():
    """create_process should leave process_ready False if the child reports init failure"""

    def factory(target=None, args: tuple = (), daemon=None):
        config = args[0]
        mock_proc = Mock()
        mock_proc.is_alive = Mock(return_value=True)
        mock_proc.start = Mock(side_effect=lambda: config.queue_result.put(False))
        return mock_proc

    with patch("Pytre_Core.sql_query.execution_runner.Process", side_effect=factory):
        runner = ExecutionRunner(AppMode.HYBRID)
        _wait_until(lambda: runner.creating_process is False)

    assert runner.process_ready is False


@pytest.mark.unit
def test_execution_runner_create_process(fake_process_factory):
    """ExecutionRunner should create process in background"""
    with patch("Pytre_Core.sql_query.execution_runner.Process", side_effect=fake_process_factory):
        runner = ExecutionRunner(AppMode.HYBRID)

        # Should have process
        _wait_until(lambda: runner.process is not None)


@pytest.mark.unit
@pytest.mark.parametrize("app_mode", [AppMode.HYBRID, AppMode.CLIENT])
def test_execution_runner_passes_app_mode_to_process(fake_process_factory, app_mode):
    """The child process gets the main process' mode instead of detecting it on its own"""
    captured_args = []

    def factory(target=None, args: tuple = (), daemon=None):
        captured_args.append(args)
        return fake_process_factory(target=target, args=args, daemon=daemon)

    with patch("Pytre_Core.sql_query.execution_runner.Process", side_effect=factory):
        runner = ExecutionRunner(app_mode)
        _wait_until(lambda: runner.process_ready is True)

    assert captured_args[0] == (captured_args[0][0], app_mode)
    assert runner.app_mode is app_mode


# ================================================================================
# Process Management Tests
# ================================================================================


@pytest.mark.unit
def test_execution_runner_kill_and_restart(fake_process_factory):
    """ExecutionRunner should terminate the process and recreate it"""
    with patch("Pytre_Core.sql_query.execution_runner.Process", side_effect=fake_process_factory):
        runner = ExecutionRunner(AppMode.HYBRID)
        _wait_until(lambda: runner.process_ready is True)

        old_process = cast(Mock, runner.process)
        old_process.join = Mock()  # avoid blocking on a mock's default join
        # simulate the process actually dying once terminate() is called
        old_process.terminate = Mock(side_effect=lambda: old_process.is_alive.configure_mock(return_value=False))

        runner.kill_and_restart()

        assert runner.task_killed is True
        assert old_process.terminate.called
        _wait_until(lambda: runner.process is not old_process)


@pytest.mark.unit
def test_execution_runner_stop_process(fake_process_factory):
    """ExecutionRunner should signal stop via event"""
    with patch("Pytre_Core.sql_query.execution_runner.Process", side_effect=fake_process_factory):
        runner = ExecutionRunner(AppMode.HYBRID)

        # Should have stop_requested event
        assert runner.stop_requested is not None

        # Stop process should set event
        runner.stop_process()

        assert runner.stop_requested.is_set()


@pytest.mark.unit
def test_execution_runner_kill_and_restart_falls_back_to_kill(fake_process_factory):
    """kill_and_restart should escalate to kill() if terminate() didn't stop the process"""
    with patch("Pytre_Core.sql_query.execution_runner.Process", side_effect=fake_process_factory):
        runner = ExecutionRunner(AppMode.HYBRID)
        _wait_until(lambda: runner.process_ready is True)

        old_process = cast(Mock, runner.process)
        old_process.join = Mock()
        # terminate() has no effect; only kill() actually stops the process
        old_process.kill = Mock(side_effect=lambda: old_process.is_alive.configure_mock(return_value=False))

        runner.kill_and_restart()

        assert old_process.terminate.called
        assert old_process.kill.called


@pytest.mark.unit
def test_execution_runner_kill_and_restart_without_process(fake_process_factory):
    """kill_and_restart should log a warning and still recreate the process if none existed"""
    with patch("Pytre_Core.sql_query.execution_runner.Process", side_effect=fake_process_factory):
        runner = ExecutionRunner(AppMode.HYBRID)
        _wait_until(lambda: runner.process_ready is True)

        runner.process = None
        runner.kill_and_restart()

        assert runner.task_killed is True
        _wait_until(lambda: runner.process is not None)


# ================================================================================
# input_task Tests
# ================================================================================


@pytest.mark.unit
def test_input_task_raises_if_creating_process(fake_process_factory):
    """input_task should raise if a process is currently being created"""
    with patch("Pytre_Core.sql_query.execution_runner.Process", side_effect=fake_process_factory):
        runner = ExecutionRunner(AppMode.HYBRID)
        _wait_until(lambda: runner.process_ready is True)

        runner.creating_process = True

        with pytest.raises(RuntimeError, match="being created"):
            runner.input_task("SRV1", {})


@pytest.mark.unit
def test_input_task_raises_if_not_ready(fake_process_factory):
    """input_task should raise if the process isn't ready yet"""
    with patch("Pytre_Core.sql_query.execution_runner.Process", side_effect=fake_process_factory):
        runner = ExecutionRunner(AppMode.HYBRID)
        _wait_until(lambda: runner.creating_process is False)
        runner.process_ready = False

        with pytest.raises(RuntimeError, match="not ready"):
            runner.input_task("SRV1", {})


@pytest.mark.unit
def test_input_task_recreates_process_if_dead(fake_process_factory):
    """input_task should recreate the process and raise if it's no longer alive"""
    with patch("Pytre_Core.sql_query.execution_runner.Process", side_effect=fake_process_factory):
        runner = ExecutionRunner(AppMode.HYBRID)
        _wait_until(lambda: runner.process_ready is True)

        process = cast(Mock, runner.process)
        process.is_alive = Mock(return_value=False)

        with pytest.raises(RuntimeError, match="not alive, recreating"):
            runner.input_task("SRV1", {})


@pytest.mark.unit
def test_input_task_queues_start_message(fake_process_factory):
    """input_task should put a 'start' message on the task queue"""
    with patch("Pytre_Core.sql_query.execution_runner.Process", side_effect=fake_process_factory):
        runner = ExecutionRunner(AppMode.HYBRID)
        _wait_until(lambda: runner.process_ready is True)

        runner.task_killed = True
        runner.input_task("SRV1", {"name": "Q1"})

        assert runner.task_killed is False
        msg = runner.queue_task.get(timeout=2)
        assert msg == ("start", "SRV1", {"name": "Q1"})


# ================================================================================
# Queue Communication Tests
# ================================================================================


@pytest.mark.unit
def test_execution_runner_queues_are_multiprocessing_queues(fake_process_factory):
    """ExecutionRunner should use real multiprocessing Queues"""
    from multiprocessing.queues import Queue as MpQueue

    with patch("Pytre_Core.sql_query.execution_runner.Process", side_effect=fake_process_factory):
        runner = ExecutionRunner(AppMode.HYBRID)

        assert isinstance(runner.queue_task, MpQueue)
        assert isinstance(runner.queue_result, MpQueue)


# ================================================================================
# Thread Safety Tests
# ================================================================================


@pytest.mark.unit
def test_execution_runner_has_lock(fake_process_factory):
    """ExecutionRunner should have lock for thread safety"""
    with patch("Pytre_Core.sql_query.execution_runner.Process", side_effect=fake_process_factory):
        runner = ExecutionRunner(AppMode.HYBRID)

        # Should have lock
        assert hasattr(runner, "_create_lock")
        assert runner._create_lock is not None


@pytest.mark.unit
def test_execution_runner_prevents_concurrent_creation(fake_process_factory):
    """ExecutionRunner should not spawn a second process while one is already being created"""
    with patch("Pytre_Core.sql_query.execution_runner.Process", side_effect=fake_process_factory):
        runner = ExecutionRunner(AppMode.HYBRID)
        _wait_until(lambda: runner.process_ready is True)

        nb_created_before = len(fake_process_factory.created)

        runner.creating_process = True
        runner.create_process()

        assert len(fake_process_factory.created) == nb_created_before


@pytest.mark.unit
def test_execution_runner_skips_creation_if_process_already_alive(fake_process_factory):
    """create_process should be a no-op if the current process is already alive"""
    with patch("Pytre_Core.sql_query.execution_runner.Process", side_effect=fake_process_factory):
        runner = ExecutionRunner(AppMode.HYBRID)
        _wait_until(lambda: runner.process_ready is True)

        nb_created_before = len(fake_process_factory.created)

        runner.create_process()  # process is already alive (mock defaults to is_alive=True)

        assert len(fake_process_factory.created) == nb_created_before


# ================================================================================
# Error Handling Tests
# ================================================================================


@pytest.mark.unit
def test_execution_runner_handles_process_init_timeout_and_kills_stuck_process():
    """
    ExecutionRunner should catch the queue.Empty timeout gracefully, then terminate
    (and if needed kill) a process that's still alive but never signaled readiness.
    """
    stuck_process = Mock()
    stuck_process.is_alive = Mock(return_value=True)  # never dies, even after terminate()

    def factory(target=None, args=(), daemon=None):
        return stuck_process

    fake_result_queue = Mock()
    fake_result_queue.get = Mock(side_effect=QueueIsEmpty())

    with (
        patch("Pytre_Core.sql_query.execution_runner.Process", side_effect=factory),
        patch("Pytre_Core.sql_query.execution_runner.Queue", side_effect=[Mock(), fake_result_queue]),
    ):
        runner = ExecutionRunner(AppMode.HYBRID)
        _wait_until(lambda: runner.creating_process is False)

    assert runner.process_ready is False
    assert stuck_process.terminate.called
    assert stuck_process.kill.called


# ================================================================================
# Integration Stubs
# ================================================================================


@pytest.mark.unit
@pytest.mark.skip(reason="Integration test - requires full setup")
def test_execution_runner_full_workflow():
    """Full workflow: create, execute, stop, restart"""
    # This would require:
    # 1. Creating runner
    # 2. Submitting task to queue
    # 3. Getting result from queue
    # 4. Stopping process
    # 5. Restarting process
    pass


@pytest.mark.unit
@pytest.mark.skip(reason="Integration test - requires database")
def test_execution_runner_executes_query():
    """ExecutionRunner should execute SQL query via process"""
    # This would require:
    # 1. Mock database connection
    # 2. Create Query object
    # 3. Submit to runner
    # 4. Verify execution
    pass


# ================================================================================
# ExecutionProcess Tests
# ================================================================================


from Pytre_Core.sql_query.execution_runner import ExecutionProcess, run_execution_process  # noqa: E402


def _make_execution_process(queue_task=None, queue_result=None):
    """Build an ExecutionProcess without going through __init__ (avoids AppContext()/UserContext())"""
    proc = ExecutionProcess.__new__(ExecutionProcess)
    proc.queue_task = queue_task if queue_task is not None else Mock()
    proc.queue_result = queue_result if queue_result is not None else Mock()
    proc.stop_requested = proc_get_event()
    proc.cannot_stop = proc_get_event()
    proc.app_ctx = Mock()
    proc.user_ctx = Mock()
    return proc


@pytest.mark.unit
def test_execution_process_init_builds_context():
    """ExecutionProcess.__init__ should route logs through the result queue and build both contexts"""
    config = ProcessConfig(
        queue_task=Mock(), queue_result=Mock(), stop_requested=proc_get_event(), cannot_stop=proc_get_event()
    )

    with (
        patch("Pytre_Core.sql_query.execution_runner.AppContext") as MockAppContext,
        patch("Pytre_Core.sql_query.execution_runner.UserContext") as MockUserContext,
        patch("Pytre_Core.sql_query.execution_runner.set_log_queue") as mock_set_log_queue,
    ):
        mock_app_ctx = Mock(acts_as_server=True)
        MockAppContext.return_value = mock_app_ctx
        mock_user_ctx = Mock()
        MockUserContext.return_value = mock_user_ctx

        proc = ExecutionProcess(config, AppMode.HYBRID)

    mock_set_log_queue.assert_called_once_with(config.queue_result)
    MockAppContext.assert_called_once_with(mode=AppMode.HYBRID)
    assert proc.app_ctx is mock_app_ctx
    assert proc.user_ctx is mock_user_ctx
    MockUserContext.assert_called_once_with(mock_app_ctx.users)
    assert proc.queue_task is config.queue_task


@pytest.mark.unit
def test_execution_process_init_in_client_mode_has_no_users_database():
    """In CLIENT mode the user context is built without a users database - app_ctx.users is never touched"""
    config = ProcessConfig(
        queue_task=Mock(), queue_result=Mock(), stop_requested=proc_get_event(), cannot_stop=proc_get_event()
    )

    with (
        patch("Pytre_Core.sql_query.execution_runner.AppContext") as MockAppContext,
        patch("Pytre_Core.sql_query.execution_runner.UserContext") as MockUserContext,
        patch("Pytre_Core.sql_query.execution_runner.set_log_queue"),
    ):
        MockAppContext.return_value = Mock(spec=["acts_as_server"], acts_as_server=False)

        ExecutionProcess(config, AppMode.CLIENT)

    MockAppContext.assert_called_once_with(mode=AppMode.CLIENT)
    MockUserContext.assert_called_once_with(None)


@pytest.mark.unit
def test_execution_process_start_aborts_if_kee_is_ko():
    """start should signal not-ready and return immediately if kee failed to open"""
    proc = _make_execution_process()
    proc.app_ctx.kee.is_ko = True

    proc.start()

    proc.queue_result.put.assert_called_once_with(False)


@pytest.mark.unit
def test_execution_process_start_in_client_mode_skips_database_check():
    """Without a KeePass database there's nothing to check - the process must not touch app_ctx.kee"""
    from queue import Queue as ThreadQueue

    proc = _make_execution_process(queue_task=ThreadQueue())
    proc.app_ctx = Mock(spec=["acts_as_server"], acts_as_server=False)
    proc.queue_task.put(("stop", "", ""))

    proc.start()

    proc.queue_result.put.assert_any_call(True)


@pytest.mark.unit
def test_execution_process_start_signals_ready_then_processes_stop():
    """start should signal ready, then exit its loop on a 'stop' message"""
    from queue import Queue as ThreadQueue

    proc = _make_execution_process(queue_task=ThreadQueue())
    proc.app_ctx.kee.is_ko = False
    proc.queue_result = Mock()
    proc.queue_task.put(("stop", "", ""))

    proc.start()

    proc.queue_result.put.assert_any_call(True)


@pytest.mark.unit
def test_execution_process_start_dispatches_to_run_task():
    """start should call run_task for a 'start' message, then stop on 'stop'"""
    from queue import Queue as ThreadQueue

    proc = _make_execution_process(queue_task=ThreadQueue())
    proc.app_ctx.kee.is_ko = False
    proc.queue_result = Mock()
    proc.run_task = Mock()
    proc.queue_task.put(("start", "SRV1", {"a": 1}))
    proc.queue_task.put(("stop", "", ""))

    proc.start()

    proc.run_task.assert_called_once_with("SRV1", {"a": 1})


@pytest.mark.unit
def test_execution_process_run_task_success():
    """run_task should deserialize the query, construct a QueryExecute wired to it (this is the
    exact call that regressed once - QueryExecute() built without passing query= at all, silently
    breaking every local execution - so the constructor args are asserted explicitly here), then
    execute it and report the result"""
    proc = _make_execution_process()
    proc.queue_result = Mock()

    fake_query = Mock()
    fake_query_execute = Mock()
    fake_query_execute.execute = Mock(return_value=(42, "/tmp/out.csv"))

    with (
        patch("Pytre_Core.sql_query.execution_runner.Query") as MockQuery,
        patch("Pytre_Core.sql_query.execution_runner.QueryExecute") as MockQueryExecute,
    ):
        MockQuery.deserialize = Mock(return_value=fake_query)
        MockQueryExecute.return_value = fake_query_execute
        proc.run_task("SRV1", {"name": "Q1"})

    MockQueryExecute.assert_called_once_with(proc.app_ctx, proc.user_ctx, query=fake_query)
    fake_query_execute.execute.assert_called_once_with(server_id="SRV1", file_output=True)
    proc.queue_result.put.assert_any_call(("result", (42, "/tmp/out.csv")))
    proc.queue_result.put.assert_any_call(("done", None))


@pytest.mark.unit
def test_execution_process_run_task_reloads_user_prefs_before_building_the_query():
    """The process lives for the whole app session - prefs changed in the UI since it started
    (csv format, cache folder...) must be picked up before the query and its config are built"""
    proc = _make_execution_process()
    proc.queue_result = Mock()
    calls = []
    proc.user_ctx.user_prefs.load = Mock(side_effect=lambda: calls.append("load"))

    with (
        patch("Pytre_Core.sql_query.execution_runner.Query") as MockQuery,
        patch("Pytre_Core.sql_query.execution_runner.QueryExecute") as MockQueryExecute,
    ):
        MockQuery.deserialize = Mock(side_effect=lambda *args, **kwargs: calls.append("deserialize") or Mock())
        MockQueryExecute.return_value.execute = Mock(return_value=(0, None))
        proc.run_task("SRV1", {"name": "Q1"})

    assert calls == ["load", "deserialize"]


@pytest.mark.unit
def test_execution_process_run_task_handles_error():
    """run_task should report an 'error' message and still send 'done' if execution raises"""
    proc = _make_execution_process()
    proc.queue_result = Mock()

    with patch("Pytre_Core.sql_query.execution_runner.Query") as MockQuery:
        MockQuery.deserialize = Mock(side_effect=ValueError("boom"))
        proc.run_task("SRV1", {})

    proc.queue_result.put.assert_any_call(("error", "boom"))
    proc.queue_result.put.assert_any_call(("done", None))


@pytest.mark.unit
def test_run_execution_process_starts_execution_process():
    """run_execution_process should build an ExecutionProcess from config and start it"""
    config = ProcessConfig(
        queue_task=Mock(), queue_result=Mock(), stop_requested=proc_get_event(), cannot_stop=proc_get_event()
    )

    with patch("Pytre_Core.sql_query.execution_runner.ExecutionProcess") as MockExecProcess:
        mock_instance = Mock()
        MockExecProcess.return_value = mock_instance

        run_execution_process(config, AppMode.CLIENT)

    MockExecProcess.assert_called_once_with(config, AppMode.CLIENT)
    mock_instance.start.assert_called_once()
