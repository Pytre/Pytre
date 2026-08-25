"""
Tests for Pytre_Client/ui_utils_thread.py
"""

import logging
import tkinter as tk
from collections.abc import Callable
from queue import Queue
from typing import cast

import pytest

from Pytre_Client.ui_utils_thread import tk_call_when_ready


class _FakeTkWindow:
    """Minimal stand-in for tk.Toplevel - only .after() is actually used by tk_call_when_ready.
    Not a real Toplevel subclass (its .after() overloads don't line up with a plain fake without
    a real Tk display) - passed to tk_call_when_ready via cast() instead. Scheduled callbacks
    are recorded rather than run automatically, so tests control exactly when each "tick" of
    the fake event loop happens."""

    def __init__(self):
        self.scheduled: list[tuple[int, Callable[[], None]]] = []

    def after(self, ms: int, fn: Callable[[], None]) -> None:
        self.scheduled.append((ms, fn))

    def run_next(self) -> None:
        """Pops and runs the oldest scheduled callback - simulates one event loop tick."""
        _, fn = self.scheduled.pop(0)
        fn()


def _as_toplevel(window: _FakeTkWindow) -> tk.Toplevel:
    return cast(tk.Toplevel, window)


@pytest.mark.unit
def test_schedules_the_first_check_with_the_given_interval():
    window = _FakeTkWindow()

    tk_call_when_ready(_as_toplevel(window), Queue(), lambda *a: None, check_interval_ms=250)

    assert len(window.scheduled) == 1
    assert window.scheduled[0][0] == 250


@pytest.mark.unit
def test_uses_default_check_interval_of_100ms():
    window = _FakeTkWindow()

    tk_call_when_ready(_as_toplevel(window), Queue(), lambda *a: None)

    assert window.scheduled[0][0] == 100


@pytest.mark.unit
def test_calls_callback_with_single_value_when_result_is_not_a_tuple():
    window = _FakeTkWindow()
    q = Queue()
    q.put("hello")
    calls = []

    tk_call_when_ready(_as_toplevel(window), q, lambda v: calls.append(v))
    window.run_next()

    assert calls == ["hello"]


@pytest.mark.unit
def test_unpacks_tuple_as_positional_arguments():
    """Regression: a 2-tuple whose second element happens to be a dict must still be passed
    positionally - a previous version misread this shape as an (args, kwargs) pair and tried
    callback(*args, **kwargs), which broke App.refresh_queries's refresh_end(queries, errors)
    since both queries and errors are plain dicts (2026-08-26)."""
    window = _FakeTkWindow()
    q = Queue()
    q.put(({"local": 1}, {"local": 2}))
    calls = []

    tk_call_when_ready(_as_toplevel(window), q, lambda *a: calls.append(a))
    window.run_next()

    assert calls == [({"local": 1}, {"local": 2})]


@pytest.mark.unit
def test_unpacks_a_plain_tuple_of_positional_args():
    window = _FakeTkWindow()
    q = Queue()
    q.put((1, "two", 3.0))
    calls = []

    tk_call_when_ready(_as_toplevel(window), q, lambda *a: calls.append(a))
    window.run_next()

    assert calls == [(1, "two", 3.0)]


@pytest.mark.unit
def test_reschedules_when_queue_is_empty():
    window = _FakeTkWindow()
    calls = []

    tk_call_when_ready(_as_toplevel(window), Queue(), lambda v: calls.append(v))
    window.run_next()

    assert calls == []
    assert len(window.scheduled) == 1  # rescheduled for the next tick


@pytest.mark.unit
def test_reschedules_using_the_same_custom_interval_when_queue_is_empty():
    window = _FakeTkWindow()

    tk_call_when_ready(_as_toplevel(window), Queue(), lambda v: None, check_interval_ms=250)
    window.run_next()

    assert window.scheduled[0][0] == 250


@pytest.mark.unit
def test_logs_and_reschedules_when_callback_raises(monkeypatch):
    window = _FakeTkWindow()
    q = Queue()
    q.put("boom")
    logged = []
    monkeypatch.setattr(
        "Pytre_Client.ui_utils_thread.log", lambda msg, level=logging.INFO: logged.append((msg, level))
    )

    def failing_callback(_):
        raise ValueError("boom")

    tk_call_when_ready(_as_toplevel(window), q, failing_callback)
    window.run_next()

    assert len(logged) == 1
    assert logged[0][1] == logging.ERROR
    assert len(window.scheduled) == 1  # rescheduled despite the error
