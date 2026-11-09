#!/usr/bin/env bash
# Host-only standalone BusyBox build; reuse existing toolchain, no USB access.
set -euo pipefail
work_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_dir=$(cd -- "$work_dir/../.." && pwd)
source_dir="$project_dir/reference/busybox-1.37.0"
build_dir="$work_dir/build"
cross="$project_dir/user/toolchain/output/host/bin/riscv32-buildroot-linux-uclibc-"
[[ -f "$source_dir/Makefile" ]] || { printf 'Run bash scripts/fetch-busybox-reference.sh first.\n' >&2; exit 1; }
python3 - "$source_dir" <<'PY'
from pathlib import Path
import hashlib,json,os,sys
source=Path(sys.argv[1]);ref=source.parent
for line in (ref/'busybox-1.37.0-source-SHA256SUMS').read_text().splitlines():
    expected,name=line.split(maxsplit=1);p=source/name
    if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:raise SystemExit('BusyBox source changed: '+name)
for name,target in json.loads((ref/'busybox-1.37.0-source-links.json').read_text()).items():
    if not (ref/name).is_symlink() or os.readlink(ref/name)!=target:raise SystemExit('BusyBox link changed: '+name)
print('PASS: BusyBox reference source files/links unchanged')
PY
mkdir -p "$build_dir"
