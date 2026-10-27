"""cds-runtime: the shared runtime API for CDS workloads (issue #219).

Application code imports only this package; backend-specific libraries
(SQLAlchemy, database drivers) stay internal, installed by the workload's
image. Imports are side-effect free: engines and connections are created
lazily, on first use.

    from cds_runtime import connections

    analytics = connections.database("analytics")
    rows = analytics.fetch_all(
        "SELECT * FROM customers WHERE active = :active",
        {"active": True},
    )
    analytics.execute(
        "INSERT INTO audit_log (message) VALUES (:message)",
        {"message": "import completed"},
    )

Credentials never appear in logs: error messages name environment
variables, not values, and connection URIs are redacted through
``redact_uri`` before display. See ``docs/runtime-api.md`` for the full
environment convention and API reference.
"""
from .connections import close_all, database
from .database import DatabaseConnection
from .errors import (
    CdsRuntimeError,
    ConnectionSetupError,
    DriverUnavailableError,
    MissingConnectionSettingsError,
    QueryExecutionError,
    UnknownConnectionError,
)
from .redaction import redact_uri, redact_uris_in_text
from .resolution import env_prefix_for, resolve_connection_uri

__version__ = "0.1.0"

__all__ = [
    "CdsRuntimeError",
    "ConnectionSetupError",
    "DatabaseConnection",
    "DriverUnavailableError",
    "MissingConnectionSettingsError",
    "QueryExecutionError",
    "UnknownConnectionError",
    "close_all",
    "database",
    "env_prefix_for",
    "redact_uri",
    "redact_uris_in_text",
    "resolve_connection_uri",
    "connections",
]
