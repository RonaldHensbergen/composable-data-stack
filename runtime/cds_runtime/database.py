"""The DatabaseConnection object: provider-neutral operations over one named connection."""
from __future__ import annotations

import importlib
import os
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlsplit

from .errors import CdsRuntimeError, DriverUnavailableError, QueryExecutionError
from .redaction import looks_like_uri, redact_secret, redact_uri
from .resolution import env_prefix_for, resolve_connection_uri

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_DEFAULT_BATCH_SIZE = 500


def _load_sqlalchemy():
    try:
        return importlib.import_module("sqlalchemy")
    except ModuleNotFoundError as exc:
        raise DriverUnavailableError(
            "SQLAlchemy is not installed; the cds-runtime database API requires it. "
            "Install it directly or via the cds-runtime[sqlalchemy] extra."
        ) from exc


def _validate_identifier(identifier: str) -> None:
    """Reject anything that is not a plain SQL identifier (injection guard)."""
    if not isinstance(identifier, str) or not _IDENTIFIER_PATTERN.match(identifier):
        raise ValueError(
            f"Invalid SQL identifier: {identifier!r}. Identifiers may contain "
            "letters, digits, and underscores, and must not start with a digit."
        )


class DatabaseConnection:
    """One named database connection, resolved lazily from the environment.

    Creating the object is side-effect free: nothing is imported, read, or
    connected until the first operation. Engines are created lazily and
    cached until ``close()``/``close_all()`` disposes them, which is also
    the rotation point: dispose, restart the process (or re-resolve), and
    the next operation picks up fresh credentials from the environment.
    """

    def __init__(
        self,
        name: str,
        *,
        prefix: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.name = name
        self._prefix = env_prefix_for(name, prefix)
        self._env = os.environ if env is None else env
        self._engine: Any = None

    @property
    def connection_uri(self) -> str:
        """The resolved SQLAlchemy connection URI (never logged; see redacted)."""
        return resolve_connection_uri(self._prefix, self._env)

    @property
    def redacted_connection_uri(self) -> str:
        """The connection URI with the password masked, safe to display."""
        return redact_uri(self.connection_uri)

    def engine(self, **engine_kwargs: Any):
        """The lazily created SQLAlchemy engine (escape hatch for adapters).

        Engine options apply at first creation only; later calls return the
        cached engine. Raises DriverUnavailableError when SQLAlchemy or the
        backend driver is missing.
        """
        if self._engine is None:
            sqlalchemy = _load_sqlalchemy()
            try:
                self._engine = sqlalchemy.create_engine(self.connection_uri, **engine_kwargs)
            except ModuleNotFoundError as exc:
                raise DriverUnavailableError(
                    f"The database driver for connection {self.name!r} is not installed: "
                    f"{exc}. Install the matching cds-runtime extra (postgresql/mysql) "
                    "or provide the driver in the workload image."
                ) from exc
        return self._engine

    def fetch_all(self, statement: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a query and return every row as a plain dict (provider-neutral)."""
        with self._begin() as connection:
            return [dict(row) for row in self._execute(connection, statement, params).mappings().all()]

    def fetch_one(self, statement: str, params: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        """Run a query and return the first row as a dict, or None."""
        with self._begin() as connection:
            row = self._execute(connection, statement, params).mappings().first()
            return dict(row) if row is not None else None

    def execute(self, statement: str, params: Mapping[str, Any] | None = None) -> int:
        """Run one statement in its own transaction and return the affected row count."""
        with self._begin() as connection:
            result = self._execute(connection, statement, params)
            rowcount = result.rowcount
            return rowcount if isinstance(rowcount, int) and rowcount >= 0 else -1

    def insert_rows(self, table: str, rows: Sequence[Mapping[str, Any]], *, batch_size: int = _DEFAULT_BATCH_SIZE) -> int:
        """Bulk-insert homogeneous rows into a table, in batches.

        Column names are validated SQL identifiers, quoted through the
        active dialect, and values are always bound parameters. All rows
        must carry the same keys. Each batch commits as one transaction;
        the total number of inserted rows is returned (or -1 when the
        driver does not report row counts for batched inserts).
        """
        if not rows:
            return 0
        if batch_size < 1:
            raise ValueError(f"batch_size must be a positive integer, got {batch_size}.")

        columns = list(rows[0])
        if not columns:
            raise ValueError("insert_rows requires at least one column.")

        parameters = []
        for index, row in enumerate(rows):
            if list(row) != columns:
                raise ValueError(
                    f"Row {index} does not carry the same keys as the first row "
                    f"({columns}); insert_rows requires homogeneous rows."
                )
            parameters.append(dict(row))

        # Identifiers are validated (rejecting injection) before the engine is
        # touched, then quoted through the active dialect: MySQL needs
        # backticks, not the double quotes its default sql_mode parses as
        # string delimiters. All values are bound parameters, so the
        # statement cannot carry injected SQL.
        for identifier in (table, *columns):
            _validate_identifier(identifier)
        preparer = self.engine().dialect.identifier_preparer
        column_list = ", ".join(preparer.quote(column) for column in columns)
        placeholders = ", ".join(f":{column}" for column in columns)
        statement = f"INSERT INTO {preparer.quote(table)} ({column_list}) VALUES ({placeholders})"  # noqa: S608

        inserted = 0
        with self._begin() as connection:
            for start in range(0, len(parameters), batch_size):
                batch = parameters[start : start + batch_size]
                result = self._execute(connection, statement, batch)
                rowcount = result.rowcount
                if not isinstance(rowcount, int) or rowcount < 0:
                    return -1
                inserted += rowcount
        return inserted

    @contextmanager
    def transaction(self) -> Iterator[_BoundConnection]:
        """Run multiple operations in one explicit transaction.

        Commits when the block exits normally, rolls back on any
        exception. The yielded object exposes fetch_all, fetch_one, and
        execute without per-statement commits.
        """
        with self.engine().begin() as connection:
            yield _BoundConnection(self, connection)

    def close(self) -> None:
        """Dispose the cached engine (idempotent). The next operation reconnects."""
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    def __enter__(self) -> DatabaseConnection:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        # Deliberately carries no configuration: never the URI or its parts.
        return f"DatabaseConnection(name={self.name!r})"

    @contextmanager
    def _begin(self):
        engine = self.engine()
        try:
            with engine.begin() as connection:
                yield connection
        except ValueError:
            # API misuse (empty statement, bad identifiers) is not a query
            # failure; surface it as the plain ValueError it is.
            raise
        except CdsRuntimeError:
            raise
        except Exception as exc:
            raise self._query_error(exc) from exc

    def _execute(self, connection: Any, statement: str, params: Any):
        if not isinstance(statement, str) or not statement.strip():
            raise ValueError("A non-empty SQL statement is required.")
        sqlalchemy = _load_sqlalchemy()
        try:
            return connection.execute(sqlalchemy.text(statement), params or {})
        except CdsRuntimeError:
            raise
        except Exception as exc:
            raise self._query_error(exc) from exc

    def _query_error(self, exc: Exception) -> QueryExecutionError:
        """Wrap a driver failure with the connection's secrets redacted."""
        message = f"{type(exc).__name__}: {exc}"
        for secret in self._known_secrets():
            message = redact_secret(message, secret)
        if looks_like_uri(message):
            message = redact_uri(message)
        return QueryExecutionError(
            f"Database operation on connection {self.name!r} failed: {message}."
        )

    def _known_secrets(self) -> list[str]:
        """The secret values this connection knows about.

        Beyond the ``_PASSWORD`` field, a password may be embedded in
        ``_CONNECTION_URI``; both are redacted from error messages.
        """
        secrets: list[str] = []
        password = self._env.get(f"{self._prefix}_PASSWORD")
        if password:
            secrets.append(password)
        connection_uri = self._env.get(f"{self._prefix}_CONNECTION_URI")
        if connection_uri:
            try:
                uri_password = urlsplit(connection_uri).password
            except ValueError:
                uri_password = None
            if uri_password:
                secrets.append(uri_password)
        return secrets


class _BoundConnection:
    """Operations bound to one open transaction (see DatabaseConnection.transaction)."""

    def __init__(self, owner: DatabaseConnection, connection: Any) -> None:
        self._owner = owner
        self._connection = connection

    def fetch_all(self, statement: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        return [dict(row) for row in self._owner._execute(self._connection, statement, params).mappings().all()]

    def fetch_one(self, statement: str, params: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        row = self._owner._execute(self._connection, statement, params).mappings().first()
        return dict(row) if row is not None else None

    def execute(self, statement: str, params: Mapping[str, Any] | None = None) -> int:
        result = self._owner._execute(self._connection, statement, params)
        rowcount = result.rowcount
        return rowcount if isinstance(rowcount, int) and rowcount >= 0 else -1
