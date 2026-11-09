#!/usr/bin/env bash
# Host build only. Experimental C5 bootstrap; no board loading.
set -euo pipefail
c5_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_dir=$(cd -- "$c5_dir/.." && pwd)
source_dir="$c5_dir/linux-6.12"
build_dir="$c5_dir/build"
[[ -f "$source_dir/Makefile" ]] || { printf 'Run bash scripts/fetch-linux-reference.sh first.\n' >&2; exit 1; }
mkdir -p "$build_dir"
[[ ${STORAGE_WRITE:-0} == 0 || ${STORAGE_WRITE:-0} == 1 ]] || { echo 'STORAGE_WRITE must be 0 or 1' >&2; exit 1; }
(cd "$c5_dir/flash-service" && sha256sum --check --status build/SHA256SUMS)
cmp "$c5_dir/flash-service/abi.h" "$source_dir/drivers/mtd/devices/c5-flash-abi.h"
cmp "$c5_dir/platform/drivers/mtd/devices/esp32c5-flash.c" "$source_dir/drivers/mtd/devices/esp32c5-flash.c"
cp "$c5_dir/flash-service/build/service.h" "$source_dir/drivers/mtd/devices/c5-flash-service.h"
test -s "$project_dir/user/rootfs/build/rootfs.cramfs"
(cd "$source_dir" && sha256sum --check --status "$c5_dir/port.sha256")
grep -q 'ro init=/init"$' "$project_dir/linux.config"
sed "s|ro init=/init\"|ro init=/init c5_flash_write=${STORAGE_WRITE:-0}\"|" \
    "$project_dir/linux.config" > "$build_dir/minimal.config"
export KCONFIG_ALLCONFIG="$build_dir/minimal.config"
