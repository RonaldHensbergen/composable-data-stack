"""Resolve named database connections from runtime-injected configuration.

A CDS profile binds each module's ``consumes`` entry to a provider's
contract at plan time, and the renderer injects the resolved fields into
the workload's environment. ``cds_runtime.connections.database("name")``
reads that configuration back at runtime through a fixed environment
variable convention, so application code never needs to know how URIs are
assembled, which backend is bound, or where the credentials live.

Convention for a connection named ``analytics`` (hyphens in a name map to
underscores, matching how binding names such as ``analytics-database``
are written in profiles):

* ``CDS_DB_ANALYTICS_CONNECTION_URI`` -- the resolved contract's
  ``connectionUri``, preferred when present.
* ``CDS_DB_ANALYTICS_BACKEND`` + fields -- assemble the URI instead:
  ``postgres``/``postgresql`` or ``mysql`` take ``_HOST``, ``_PORT``
  (numeric, default per backend), ``_DATABASE``, ``_USERNAME``,
  ``_PASSWORD`` (set but empty for password-less authentication);
  ``sqlite`` takes ``_SQLITE_PATH``.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import quote_plus

from .errors import MissingConnectionSettingsError, UnknownConnectionError

DEFAULT_ENV_PREFIX = "CDS_DB_"

_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

_PORT_PATTERN = re.compile(r"[0-9]+")

_DEFAULT_PORTS = {
    "postgres": "5432",
    "postgresql": "5432",
    "mysql": "3306",
}


def env_prefix_for(name: str, prefix: str | None = None) -> str:
    """Environment variable prefix for a named connection.

    The default prefix is ``CDS_DB_<NAME>`` with the name uppercased and
    hyphens mapped to underscores; ``prefix`` overrides it (for example to
    adopt an existing ``CDS_ANALYTICS_DB`` style environment block).
    """
    if not isinstance(name, str) or not _NAME_PATTERN.match(name):
        raise ValueError(
            f"Invalid database connection name: {name!r}. "
            "Names may contain letters, digits, hyphens and underscores."
        )
    if prefix is not None:
        return prefix
    return f"{DEFAULT_ENV_PREFIX}{name.upper().replace('-', '_')}"


#: The exact environment variable names (relative to a connection prefix)
#: that make up a connection's runtime configuration. Used to tell "no
#: configuration" (UnknownConnectionError) apart from "configuration that a
#: longer, differently-named connection happens to start with"
#: (e.g. CDS_DB_ANALYTICS_DB_HOST belongs to connection "analytics-db",
#: not to connection "analytics").
_SETTING_FIELDS = (
    "CONNECTION_URI",
    "BACKEND",
    "HOST",
    "PORT",
    "DATABASE",
    "USERNAME",
    "PASSWORD",
    "SQLITE_PATH",
)


def _has_any_setting(prefix: str, env: Mapping[str, str]) -> bool:
    return any(f"{prefix}_{field}" in env for field in _SETTING_FIELDS)


def resolve_connection_uri(prefix: str, env: Mapping[str, str]) -> str:
    """Assemble the SQLAlchemy connection URI for one connection prefix.

    ``<PREFIX>_CONNECTION_URI`` wins when set; otherwise the URI is built
    from ``<PREFIX>_BACKEND`` and the backend-specific fields. Failures
    raise typed errors that name missing environment variables, never
    their values.
    """
    if not _has_any_setting(prefix, env):
        raise UnknownConnectionError(
            f"No runtime configuration found for database connection with prefix "
            f"{prefix}; expected environment variables such as {prefix}_CONNECTION_URI "
            f"or {prefix}_HOST."
        )

    connection_uri = env.get(f"{prefix}_CONNECTION_URI")
    if connection_uri:
        return connection_uri

    declared_backend = env.get(f"{prefix}_BACKEND")
    if declared_backend is not None:
        backend = declared_backend.strip().lower()
    elif env.get(f"{prefix}_SQLITE_PATH"):
        backend = "sqlite"
    else:
        backend = "postgres"
    if backend in {"postgres", "postgresql"}:
        return _build_network_uri(prefix, "postgresql", backend, env)
    if backend == "mysql":
        return _build_network_uri(prefix, "mysql+pymysql", backend, env)
    if backend == "sqlite":
        return _build_sqlite_uri(prefix, env)
    raise MissingConnectionSettingsError(
        f"Unsupported database backend {backend!r} for connection with prefix {prefix} "
        f"({prefix}_BACKEND); supported backends are postgres, mysql, and sqlite."
    )


def _build_network_uri(prefix: str, scheme: str, backend: str, env: Mapping[str, str]) -> str:
    host = env.get(f"{prefix}_HOST")
    port = env.get(f"{prefix}_PORT") or _DEFAULT_PORTS[backend]
    database = env.get(f"{prefix}_DATABASE")
    username = env.get(f"{prefix}_USERNAME")
    password = env.get(f"{prefix}_PASSWORD")

    if not _PORT_PATTERN.fullmatch(port):
        raise MissingConnectionSettingsError(
            f"Database connection with prefix {prefix} has an invalid "
            f"{prefix}_PORT: expected a port number."
        )

    # HOST/DATABASE/USERNAME must carry a value; PASSWORD must merely be
    # set, so password-less authentication is expressible with an empty
    # value (anything more belongs in CONNECTION_URI).
    required = {
        f"{prefix}_HOST": not host,
        f"{prefix}_DATABASE": not database,
        f"{prefix}_USERNAME": not username,
        f"{prefix}_PASSWORD": f"{prefix}_PASSWORD" not in env,
    }
    missing = [name for name, is_missing in required.items() if is_missing]
    if missing:
        raise MissingConnectionSettingsError(
            f"Database connection with prefix {prefix} is missing required settings: "
            f"{', '.join(missing)}."
        )

    credentials = f"{quote_plus(username)}:{quote_plus(password)}"
    return f"{scheme}://{credentials}@{host}:{port}/{quote_plus(database)}"


def _build_sqlite_uri(prefix: str, env: Mapping[str, str]) -> str:
    path = env.get(f"{prefix}_SQLITE_PATH")
    if not path:
        raise MissingConnectionSettingsError(
            f"Database connection with prefix {prefix} is missing required settings: "
            f"{prefix}_SQLITE_PATH."
        )
    if path == ":memory:":
        return "sqlite:///:memory:"
    return f"sqlite:///{path}"
