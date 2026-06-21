# Vault secrets provider

This module provides a local Vault dev container for profiles that need a secrets backend.

It exposes a `secrets-provider` contract and runs Vault in dev mode on port 8200.

Profiles can bootstrap the Vault root token from `spec.secrets` and then use Vault for runtime secret storage.
