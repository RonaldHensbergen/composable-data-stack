#!/bin/sh
set -eu

# Required connection settings, provided by the module's compose environment
# (sourced from whichever contract is consumed -- sql-database via
# ${bindings.*} for postgres, or file-database for duckdb -- see
# module.yaml's config.warehouseType).
: "${DBT_WAREHOUSE_TYPE:?DBT_WAREHOUSE_TYPE is required}"
: "${DBT_SCHEMA:?DBT_SCHEMA is required}"

case "$DBT_WAREHOUSE_TYPE" in
    postgres)
        : "${DBT_HOST:?DBT_HOST is required when DBT_WAREHOUSE_TYPE=postgres -- set config.targetDatabase.contractRef}"
        : "${DBT_PORT:?DBT_PORT is required when DBT_WAREHOUSE_TYPE=postgres -- set config.targetDatabase.contractRef}"
        : "${DBT_DBNAME:?DBT_DBNAME is required when DBT_WAREHOUSE_TYPE=postgres -- set config.targetDatabase.contractRef}"
        : "${DBT_USER:?DBT_USER is required when DBT_WAREHOUSE_TYPE=postgres -- set config.targetDatabase.contractRef}"
        : "${DBT_PASSWORD:?DBT_PASSWORD is required when DBT_WAREHOUSE_TYPE=postgres -- set config.targetDatabase.contractRef}"
        ;;
    duckdb)
        : "${DBT_DUCKDB_PATH:?DBT_DUCKDB_PATH is required when DBT_WAREHOUSE_TYPE=duckdb -- set config.targetWarehouseFile.contractRef}"
        ;;
    *)
        echo "Unsupported DBT_WAREHOUSE_TYPE '$DBT_WAREHOUSE_TYPE' (expected postgres or duckdb)" >&2
        exit 1
        ;;
esac

mkdir -p "$DBT_PROFILES_DIR"
cp /app/images/dbt/profiles.yml "$DBT_PROFILES_DIR/profiles.yml"

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
