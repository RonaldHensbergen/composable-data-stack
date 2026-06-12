"""
Secret resolution from .env files and environment.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .diagnostics import Diagnostic


def load_secrets_from_env(env_file: Path | None = None) -> tuple[dict[str, str], list[Diagnostic]]:
    """
    Load secrets from .env file and environment variables.
    
    Priority (lowest to highest):
    1. .env file (if provided)
    2. Environment variables
    
    Args:
        env_file: Optional path to .env file. If None, looks for .env in current directory.
        
    Returns:
        Tuple of (secrets_dict, diagnostics)
    """
    diagnostics: list[Diagnostic] = []
    secrets: dict[str, str] = {}
    
    # Determine .env file path
    if env_file is None:
        env_file = Path(".env")
    else:
        env_file = Path(env_file)
    
    # Load from .env file if it exists
    if env_file.exists():
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.rstrip("\n\r")
                    
                    # Skip empty lines and comments
                    if not line or line.startswith("#"):
                        continue
                    
                    # Parse KEY=VALUE
                    if "=" not in line:
                        diagnostics.append(
                            Diagnostic(
                                level="warning",
                                code="W090",
                                message=f'Invalid .env line format: "{line}" (expected KEY=VALUE)',
                                path=f"{env_file}:{line_num}",
                            )
                        )
                        continue
                    
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip()
                    
                    # Remove quotes if present
                    if (value.startswith('"') and value.endswith('"')) or \
                       (value.startswith("'") and value.endswith("'")):
                        value = value[1:-1]
                    
                    if key:
                        secrets[key] = value
        except (IOError, OSError) as e:
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E080",
                    message=f"Failed to read .env file: {e}",
                    path=str(env_file),
                )
            )
            return {}, diagnostics
    
    # Override with environment variables
    # This allows CLI environment variables to take precedence
    for key, value in os.environ.items():
        if key.startswith("CDS_"):
            secrets[key] = value
    
    return secrets, diagnostics


def resolve_secret(key: str, secrets: dict[str, str], required: bool = False) -> tuple[str | None, Diagnostic | None]:
    """
    Resolve a single secret by key.
    
    Args:
        key: Secret key (e.g., "CDS_POSTGRES_PASSWORD")
        secrets: Dictionary of available secrets
        required: If True, emit error if secret is missing
        
    Returns:
        Tuple of (value, diagnostic). Value is None if not found.
    """
    if key in secrets:
        return secrets[key], None
    
    if required:
        return None, Diagnostic(
            level="error",
            code="E081",
            message=f'Required secret "{key}" not found in .env or environment',
            path=f"secrets.{key}",
        )
    
    return None, None


def load_profile_secrets(spec_secrets: dict[str, Any] | None, env_file: Path | None = None) -> tuple[dict[str, str], list[Diagnostic]]:
    """
    Load and resolve profile-defined secrets from .env/environment.

    Args:
        spec_secrets: The profile spec.secrets object.
        env_file: Optional path to .env file.

    Returns:
        Tuple of (resolved_secrets, diagnostics)
    """
    diagnostics: list[Diagnostic] = []
    secrets, secret_diags = load_secrets_from_env(env_file)
    diagnostics.extend(secret_diags)

    if not isinstance(spec_secrets, dict):
        return secrets, diagnostics

    values = spec_secrets.get("values", {})
    if not isinstance(values, dict):
        return secrets, diagnostics

    for secret_name, secret_def in values.items():
        if not isinstance(secret_def, dict):
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E082",
                    message=f'Secret definition "{secret_name}" must be an object.',
                    path=f"spec.secrets.values.{secret_name}",
                )
            )
            continue

        env_name = secret_def.get("env")
        required = secret_def.get("required", False)

        if not isinstance(env_name, str) or not env_name:
            diagnostics.append(
                Diagnostic(
                    level="error",
                    code="E082",
                    message=f'Secret definition "{secret_name}" must include a valid env name.',
                    path=f"spec.secrets.values.{secret_name}.env",
                )
            )
            continue

        secret_value, err = resolve_secret(env_name, secrets, required)
        if err:
            diagnostics.append(err)
            continue

        if secret_value is not None:
            secrets[secret_name] = secret_value

    return secrets, diagnostics
