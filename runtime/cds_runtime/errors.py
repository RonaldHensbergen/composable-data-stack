"""Typed errors for the cds-runtime API.

Every error message is constructed so it never carries credentials or
unredacted connection URIs: resolution failures name the missing
*environment variables*, not their values, and query failures wrap the
underlying driver error with known secret values redacted.
"""


class CdsRuntimeError(Exception):
    """Base class for every error raised by cds-runtime."""


class UnknownConnectionError(CdsRuntimeError):
    """No runtime configuration exists for the requested connection name."""


class MissingConnectionSettingsError(CdsRuntimeError):
    """The connection's configuration is incomplete or unsupported.

    The message names the missing environment variables, never their
    values, so it is safe to print in logs.
    """


class DriverUnavailableError(CdsRuntimeError):
    """SQLAlchemy or the connection's database driver is not installed."""


class QueryExecutionError(CdsRuntimeError):
    """A database operation failed.

    The wrapped driver message has known secret values (the connection
    password) redacted before it reaches this error.
    """
