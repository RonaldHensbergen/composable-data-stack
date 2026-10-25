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

    Example::

        from cds_runtime.adapters import dagster

        dagster_postgres_storage = {
            "postgres": {
                "postgresql_url": dagster.postgres_url("analytics-database"),
            }
        }
    """
    return connections.database(name, env=env).connection_uri
