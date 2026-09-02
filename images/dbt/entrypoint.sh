#!/bin/sh
set -eu

# Required connection settings, provided by the module's compose environment
# (sourced from the consumed sql-database contract via ${bindings.*}).
: "${DBT_HOST:?DBT_HOST is required}"
: "${DBT_PORT:?DBT_PORT is required}"
: "${DBT_DBNAME:?DBT_DBNAME is required}"
: "${DBT_USER:?DBT_USER is required}"
: "${DBT_PASSWORD:?DBT_PASSWORD is required}"
: "${DBT_SCHEMA:?DBT_SCHEMA is required}"

mkdir -p "$DBT_PROFILES_DIR"
cp /app/profiles.yml.template "$DBT_PROFILES_DIR/profiles.yml"

if [ -z "${DBT_COMMANDS:-}" ]; then
    echo "DBT_COMMANDS is not set; nothing to run" >&2
    exit 1
fi

# DBT_COMMANDS is a newline-separated list of dbt subcommands (e.g. "run",
# "test", "docs generate"), run in order in the current (main) shell so that
# `set -e` stops the whole entrypoint on the first failing command instead of
# only failing a piped subshell.
old_ifs="$IFS"
IFS='
'
for cmd in $DBT_COMMANDS; do
    IFS="$old_ifs"
    [ -z "$cmd" ] && continue
    echo "+ dbt $cmd --target-path $DBT_TARGET_PATH --log-path $DBT_LOG_PATH"
    # shellcheck disable=SC2086
    dbt $cmd \
        --profiles-dir "$DBT_PROFILES_DIR" \
        --project-dir "$DBT_PROJECT_DIR" \
        --target-path "$DBT_TARGET_PATH" \
        --log-path "$DBT_LOG_PATH"
    IFS='
'
done
IFS="$old_ifs"
