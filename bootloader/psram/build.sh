#!/bin/sh
set -eu
exec make -C "$(dirname "$0")/../.." ram-loader
