<#
.SYNOPSIS
    PowerShell task runner parity for the project's Makefile.

.DESCRIPTION
    Windows contributors without `make` can use this script to run the
    core developer tasks. Mirrors these Linux/macOS Makefile targets:
    install, validate, validate-profile, package. The Makefile itself is
    unchanged; this script exists alongside it as a Windows equivalent.

.PARAMETER Target
    The task to run: install, validate, validate-profile, package, or help.
    Defaults to help.

.PARAMETER ProfilePath
    Profile path used by the 'validate' target. Defaults to the local
    Dagster/Postgres/Superset profile.

.PARAMETER ValidateProfile
    Profile path required by the 'validate-profile' target. Alias: -P.

.EXAMPLE
    .\Makefile.ps1 install

.EXAMPLE
    .\Makefile.ps1 validate

.EXAMPLE
    .\Makefile.ps1 validate-profile -P profiles/local-dagster-postgres-superset/profile.yaml

.EXAMPLE
    .\Makefile.ps1 package

.EXAMPLE
    .\Makefile.ps1 help
#>

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Target = "help",

    [Parameter()]
    [string]$ProfilePath = "profiles/local-dagster-postgres-superset/profile.yaml",

    [Parameter()]
    [Alias("P")]
    [string]$ValidateProfile
)

$ErrorActionPreference = "Stop"

function Invoke-Install {
    python -m pip install -e .
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Invoke-Validate {
    cds validate $ProfilePath
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Invoke-ValidateProfile {
    if (-not $ValidateProfile) {
        Write-Host "Usage: .\Makefile.ps1 validate-profile -P profiles\...\profile.yaml"
        exit 1
    }
    cds validate $ValidateProfile
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Invoke-Package {
    python -m pip install --upgrade build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    python -m build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Show-Help {
    Write-Host "Available targets:"
    Write-Host "  install           Install the package in editable mode"
    Write-Host "  validate          Validate the default profile (-ProfilePath to override)"
    Write-Host "  validate-profile  Validate a specific profile (requires -P <path>)"
    Write-Host "  package           Build distribution packages"
    Write-Host "  help              Show this help message"
    Write-Host ""
    Write-Host "Note: lint and docker-build are not ported here. Use pre-commit"
    Write-Host "(pip install pre-commit; pre-commit run --all-files) for lint"
    Write-Host "checks on any platform, and Docker Desktop's 'docker build' works"
    Write-Host "the same way on Windows as it does on Linux/macOS."
}

switch ($Target) {
    "install"          { Invoke-Install }
    "validate"         { Invoke-Validate }
    "validate-profile" { Invoke-ValidateProfile }
    "package"          { Invoke-Package }
    "help"             { Show-Help }
    default {
        Write-Host "Unknown target: $Target"
        Show-Help
        exit 1
    }
}
