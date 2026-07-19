#!/bin/bash
set -e

exec superset run -p 8088 --host 0.0.0.0
