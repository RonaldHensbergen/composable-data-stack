"""The ``connections`` entry point: named database connections for workloads.

    from cds_runtime import connections

    analytics = connections.database("analytics")
    rows = analytics.fetch_all("SELECT * FROM customers WHERE active = :active", {"active": True})

See ``resolution`` for the environment convention and ``database`` for the
connection object's API.
"""
from __future__ import annotations

import threading
from collections.abc import Mapping

from .database import DatabaseConnection
from .resolution import env_prefix_for, resolve_connection_uri

__all__ = ["close_all", "database", "env_prefix_for", "resolve_connection_uri", "DatabaseConnection"]

# Registry of connections resolved from the process environment, keyed by
# (name, environment prefix): a connection is identified by both its name
# (used in errors) and where its configuration comes from, so two names
# sharing a prefix never share a connection object. Passing an explicit
# ``env`` mapping (tests, adapters with their own environment) always builds
# a fresh object instead of touching this registry. The registry is
# lock-guarded (double-checked): concurrent first calls return one shared
# connection, so a cold registry cannot leak engines through the
# create-then-store window.
_connections: dict[tuple[str, str], DatabaseConnection] = {}
_registry_lock = threading.Lock()


def database(
    name: str,
    *,
    prefix: str | None = None,
    env: Mapping[str, str] | None = None,
) -> DatabaseConnection:
    """Return the named database connection, creating it lazily on first use.

    Connections resolved from the process environment are cached by their
    name and environment prefix, so repeated calls share one engine.
    ``prefix`` overrides the default ``CDS_DB_<NAME>`` variable prefix (e.g.
    to adopt an existing ``CDS_ANALYTICS_DB`` style environment block);
    ``env`` supplies the configuration explicitly and bypasses the registry.
    """
    if env is not None:
        return DatabaseConnection(name, prefix=prefix, env=env)
    key = (name, env_prefix_for(name, prefix))
    connection = _connections.get(key)
    if connection is None:
        with _registry_lock:
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
    with _registry_lock:
        for connection in _connections.values():
            connection.close()
        _connections.clear()
