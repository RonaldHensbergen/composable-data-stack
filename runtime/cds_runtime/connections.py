"""The ``connections`` entry point: named database connections for workloads.

    from cds_runtime import connections

    analytics = connections.database("analytics")
    rows = analytics.fetch_all("SELECT * FROM customers WHERE active = :active", {"active": True})

See ``resolution`` for the environment convention and ``database`` for the
connection object's API.
"""
from __future__ import annotations

from collections.abc import Mapping

from .database import DatabaseConnection
from .resolution import env_prefix_for, resolve_connection_uri

__all__ = ["close_all", "database", "env_prefix_for", "resolve_connection_uri", "DatabaseConnection"]

# Registry of connections resolved from the process environment. Passing an
# explicit ``env`` mapping (tests, adapters with their own environment)
# always builds a fresh object instead of touching this registry.
_connections: dict[str, DatabaseConnection] = {}


def database(
    name: str,
    *,
    prefix: str | None = None,
    env: Mapping[str, str] | None = None,
) -> DatabaseConnection:
    """Return the named database connection, creating it lazily on first use.

    Connections resolved from the process environment are cached by their
    environment prefix, so repeated calls share one engine. ``prefix``
    overrides the default ``CDS_DB_<NAME>`` variable prefix (e.g. to adopt
    an existing ``CDS_ANALYTICS_DB`` style environment block); ``env``
    supplies the configuration explicitly and bypasses the registry.
    """
    if env is not None:
        return DatabaseConnection(name, prefix=prefix, env=env)
    key = env_prefix_for(name, prefix)
    connection = _connections.get(key)
    if connection is None:
        connection = DatabaseConnection(name, prefix=prefix)
        _connections[key] = connection
    return connection


def close_all() -> None:
    """Dispose every registered connection's engine and clear the registry.

    This is the in-process rotation point: after ``close_all()``, the next
    ``database(...)`` call re-reads the environment and reconnects with
    fresh credentials.
    """
    for connection in _connections.values():
        connection.close()
    _connections.clear()
