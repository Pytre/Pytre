from typing import Any, Protocol
from collections.abc import Iterator


class SqlRow(Protocol):
    """A single row from a SELECT query result."""

    def to_dict(self) -> dict[str, Any]: ...


class AsyncSqlResult(Protocol):
    """Result from SELECT query. Rows expose to_dict() rather than a full Mapping interface ;
    fastmssql's FastRow has no .keys()/.items(), only to_dict()/get()/get_by_index()/values()."""

    def __iter__(self) -> Iterator[SqlRow]: ...
    def columns(self) -> list[str]: ...


class AsyncSqlDriver(Protocol):
    """Async SQL Driver : fastmssql for now, designed to allow asyncpg in a later phase."""

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def execute(self, template: str, params: list[Any]) -> AsyncSqlResult: ...
