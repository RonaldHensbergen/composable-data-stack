# KeyDB

KeyDB cache layer. High-performance Redis-compatible cache for session
storage, caching, and message passing.

## Purpose

Provides an in-memory cache for profiles that need a Redis-compatible
cache service, for example the session and query cache of a BI module.

## Known limitations

- Data is ephemeral, no persistence volume is attached
- The host port is bound to `127.0.0.1` only, so the cache is not exposed to other hosts

## Upstream documentation

- [KeyDB documentation](https://docs.keydb.dev/)

## Configuration notes

- `password` defaults to empty, set it in the profile when authentication is needed
- The image is pinned to `eqalpha/keydb:6.3.4` with a SHA digest
