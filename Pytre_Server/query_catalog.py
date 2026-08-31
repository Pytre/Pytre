import typing
from dataclasses import dataclass
from pathlib import Path

from Pytre_Core.sql_query.file_parser import FileParser
from Pytre_Core.sql_query.loader import load_files


@dataclass(frozen=True)
class QueryCatalog:
    """Immutable snapshot of the query folder's file listing (disk I/O only,
    no user-specific data). Refreshing means building a new instance and
    swapping app.state.query_catalog - never mutating in place - so
    in-flight requests keep a consistent view with no locking needed."""

    parsers: list[FileParser]
    load_errors: list[str]
    by_file_id: dict[str, FileParser]  # query file_id -> parser

    @classmethod
    def load(cls, folder: Path) -> typing.Self:
        parsers, errors = load_files(folder)
        by_file_id = {p.filename.stem: p for p in parsers}
        return cls(parsers=parsers, load_errors=errors, by_file_id=by_file_id)
