"""Dagster adapter: named connections as Dagster-native configuration.

Dagster's PostgreSQL storage and IO managers accept a connection URL in
their run/instance configuration. This adapter resolves a named CDS
connection into that URL, so a Dagster workload configured in Python can
point its storage at a bound ``sql-database`` contract without knowing how
the URI was assembled.
"""
from __future__ import annotations

from collections.abc import Mapping

from .. import connections


def postgres_url(name: str, *, env: Mapping[str, str] | None = None) -> str:
    """The connection URL for Dagster PostgreSQL storage configuration.

    Raises ``ValueError`` when the named connection does not resolve to a
    PostgreSQL URL, so a mis-bound contract fails here instead of inside
    Dagster's storage machinery.

    Example::

        from cds_runtime.adapters import dagster

        dagster_postgres_storage = {
            "postgres": {
                "postgresql_url": dagster.postgres_url("analytics-database"),
            }
        }
    """
    url = connections.database(name, env=env).connection_uri
    scheme = url.partition(":")[0].lower()
    if not scheme.startswith("postgres"):
        raise ValueError(
            f"Connection {name!r} does not resolve to a PostgreSQL URL "
            f"(scheme {scheme!r}); Dagster PostgreSQL storage requires a "
            "postgresql:// connection."
        )
    return url
