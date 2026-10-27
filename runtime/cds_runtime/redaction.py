"""Redaction helpers.

cds-runtime never logs credentials or unredacted connection URIs. These
helpers produce display-safe strings: a redacted URI keeps its scheme,
host, and database but masks the password, and secret values are removed
from arbitrary text (driver error messages) before they are surfaced.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

_SECRET_QUERY_KEYS = ("password", "passphrase", "secret", "token", "key")

# Secrets shorter than this are not substituted into messages: replacing a
# 1-2 character value would mangle unrelated words in driver errors (e.g. a
# one-character password appearing inside a column name), and such short
# values carry no meaningful secrecy.
_MIN_SECRET_LENGTH = 3


def redact_secret(text: str, secret: str | None) -> str:
    """Remove a known secret value from arbitrary text.

    Secrets shorter than three characters are left alone: substituting
    them would corrupt unrelated words in driver messages, and a value
    that short is not meaningfully protective.
    """
    if not secret or len(secret) < _MIN_SECRET_LENGTH:
        return text
    return str(text).replace(secret, "***")


def redact_uri(uri: str) -> str:
    """Mask the password (and secret-looking query parameters) of a URI."""
    parts = urlsplit(uri)
    netloc = parts.netloc
    if "@" in netloc:
        credentials, _, host = netloc.rpartition("@")
        user, sep, _password = credentials.partition(":")
        if sep:
            netloc = f"{user}:***@{host}"
    query = "&".join(
        f"{key}=***" if key.lower() in _SECRET_QUERY_KEYS and value else f"{key}={value}"
        for key, _, value in (item.partition("=") for item in parts.query.split("&") if item)
    )
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def looks_like_uri(value: str) -> bool:
    """Best-effort check used to keep URIs out of error messages."""
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value))


#: URI-shaped substrings inside arbitrary text (driver error messages can
#: echo the connection URL anywhere in the message, not just at the start).
_URI_IN_TEXT = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s'\"]+")


def redact_uris_in_text(text: str) -> str:
    """Mask the password of every URI-shaped substring in arbitrary text.

    Driver errors sometimes quote the connection URL mid-message, where
    ``looks_like_uri`` (anchored at the start of the string) never fires;
    this helper finds and redacts each such substring in place.
    """
    return _URI_IN_TEXT.sub(lambda match: redact_uri(match.group(0)), str(text))
