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
cmp "$c5_dir/overlay/drivers/mtd/devices/esp32c5-flash.c" "$source_dir/drivers/mtd/devices/esp32c5-flash.c"
cp "$c5_dir/flash-service/build/service.h" "$source_dir/drivers/mtd/devices/c5-flash-service.h"
test -s "$project_dir/user/rootfs/build/rootfs.cramfs"
python3 - "$c5_dir" <<'PYCHECK'
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1])
for name,entry in json.loads((root/'source-hashes.json').read_text()).items():
    if hashlib.sha256((root/'linux-6.12'/name).read_bytes()).hexdigest()!=entry['modified']:
        raise SystemExit('Modified source differs from reviewed patch: '+name)
PYCHECK
# Expand the initramfs path for this checkout; keep the committed seed portable.
python3 - "$project_dir/linux.config" "$build_dir/minimal.config" "$project_dir" <<'CONFIGPATH'
from pathlib import Path
import sys
import os
s=Path(sys.argv[1]).read_text().replace('@PROJECT@',sys.argv[3])
s=s.replace('ro init=/init"','ro init=/init c5_flash_write='+os.environ.get('STORAGE_WRITE','0')+'"')
Path(sys.argv[2]).write_text(s)
CONFIGPATH
export KCONFIG_ALLCONFIG="$build_dir/minimal.config"
