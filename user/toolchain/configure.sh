#!/usr/bin/env bash
# Project-local user toolchain only; no C5/USB or kernel writes.
set -euo pipefail
toolchain_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_dir=$(cd -- "$toolchain_dir/../.." && pwd)
source_dir="$project_dir/reference/buildroot-2025.02.19"
output_dir="$toolchain_dir/output"
action=${1:---build}
case "$action" in --configure|--build|--download) ;; *) printf 'Usage: bash user/toolchain/build.sh [--configure|--download|--build]\n' >&2; exit 2 ;; esac
[[ -f "$source_dir/Makefile" ]] || { printf 'Run bash scripts/fetch-buildroot-reference.sh first.\n' >&2; exit 1; }
# Buildroot's own host dependency checks run below; never install packages here.
python3 - "$source_dir" <<'PY'
from pathlib import Path
import hashlib,json,os,sys
source=Path(sys.argv[1]);ref=source.parent
metadata=json.loads((ref/'buildroot-2025.02.19-source.json').read_text())
if metadata['archive_sha256']!='e5e58bff1da91f022f60cefdfe280140c08023cd0aeec1e5f6f9da0ea866d69c':
    raise SystemExit('Unexpected pinned Buildroot archive digest')
for line in (ref/'buildroot-2025.02.19-source-SHA256SUMS').read_text().splitlines():
    expected,name=line.split(maxsplit=1);path=source/name
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
        raise SystemExit('Reference source changed: '+name)
for name,target in json.loads((ref/'buildroot-2025.02.19-source-links.json').read_text()).items():
    path=ref/name
    if not path.is_symlink() or os.readlink(path)!=target:raise SystemExit('Reference symlink changed: '+name)
print('PASS: downloaded Buildroot reference files and symlink targets unchanged')
PY
mkdir -p "$output_dir" "$toolchain_dir/logs"
python3 - "$toolchain_dir" "$project_dir" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1]);project=sys.argv[2]
text=(root/'c5_defconfig').read_text().replace('@PROJECT@',project)
(root/'output/c5_defconfig').write_text(text)
PY
