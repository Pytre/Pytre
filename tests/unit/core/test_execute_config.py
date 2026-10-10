"""
Tests for Pytre/sql_query/execute_config.py
"""

import dataclasses

import pytest

from Pytre_Core.sql_query.execute_config import LocalExecuteCfg, RemoteExecuteCfg
from Pytre_Core.user_prefs import CsvFormat, DisplayFormat


@pytest.mark.unit
@pytest.mark.parametrize("config_class", [LocalExecuteCfg, RemoteExecuteCfg])
def test_csv_format_defaults(config_class):
    assert config_class().csv_format == CsvFormat()


@pytest.mark.unit
@pytest.mark.parametrize("config_class", [LocalExecuteCfg, RemoteExecuteCfg])
def test_csv_format_is_kept_as_given(config_class):
    csv_format = CsvFormat(delimiter="|", quotechar="'", encoding="latin-1")

    assert config_class(csv_format=csv_format).csv_format is csv_format


@pytest.mark.unit
def test_remote_display_format_defaults():
    assert RemoteExecuteCfg().display_format == DisplayFormat()


@pytest.mark.unit
def test_config_is_frozen():
    config = LocalExecuteCfg()

    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(config, "csv_format", CsvFormat())
