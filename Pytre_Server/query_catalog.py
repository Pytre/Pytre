import typing
from dataclasses import dataclass, field, replace
from pathlib import Path

from Pytre_Core.context import AppContext, UserContext
from Pytre_Core.sql_query.file_parser import FileParser
from Pytre_Core.sql_query.loader import build_queries_from_parsers, load_files


@dataclass(frozen=True)
class QueryCatalog:
    """Immutable snapshot of the query folder's file listing (disk I/O only,
    no user-specific data). Refreshing means building a new instance and
    swapping app.state.query_catalog - never mutating in place - so
    in-flight requests keep a consistent view with no locking needed."""

    parsers: list[FileParser]
    by_file_id: dict[str, FileParser]  # query file_id -> parser
    load_errors: list[str]
    build_errors: list[str] = field(default_factory=list)  # filled separately, see with_build_errors()

    @classmethod
    def load(cls, folder: Path) -> typing.Self:
        parsers, errors = load_files(folder)
        by_file_id = {p.filename.stem: p for p in parsers}
        return cls(parsers=parsers, load_errors=errors, by_file_id=by_file_id)

    @classmethod
    def create_empty(cls, error: str) -> typing.Self:
        """Catalog without any query, `error` telling why (e.g. a missing folder at startup)."""
        return cls(parsers=[], load_errors=[error], by_file_id={})

    def with_build_errors(self, app_ctx: AppContext, user_ctx: UserContext) -> typing.Self:
        """Returns a copy of this catalog with build_errors filled, by attempting to build a
        Query from every parser as user_ctx would see it - surfaces structurally broken queries
        (bad param/template syntax...), not a full per-user validation (a query could still
        fail only for a different user, e.g. a missing USER_INFO attribute)."""
        _queries, build_errors = build_queries_from_parsers(app_ctx, user_ctx, self.parsers)
        return replace(self, build_errors=build_errors)
