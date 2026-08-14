"""
Tests for Pytre/sql_query/execute_config.py

ExecuteConfig is a plain dataclass; its behavior is exercised indirectly through
QueryExecute's tests (see test_query_execute.py), which construct and mutate it
extensively. No dedicated standalone tests existed for it in the original test suite.
"""

from Pytre_Core.sql_query.execute_config import LocalExecuteCfg  # noqa: F401
