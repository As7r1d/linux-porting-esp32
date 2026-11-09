#!/bin/sh
# Optional one-command host build, matching the documented make stages.
set -eu
cd "$(dirname "$0")"
make fetch
make all "$@"
