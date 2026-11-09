#!/usr/bin/env bash
# Host source only; preserve the hardware-qualified kernel and toolchain.
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
version=1.37.0
reference_dir="$project_dir/reference"
name="busybox-$version"
archive="$name.tar.bz2"
source_dir="$reference_dir/$name"
if [[ -e "$source_dir" ]]; then
    printf 'Source already exists; preserving it: %s\n' "$source_dir"
    exit 0
fi
mkdir -p "$reference_dir"
staging_dir=$(mktemp -d "$reference_dir/.busybox-download.XXXXXXXX")
trap 'rm -rf -- "$staging_dir"' EXIT
if [[ ! -s "$reference_dir/$archive" ]]; then
    curl --fail --location --proto '=https' --retry 2 --connect-timeout 20 --max-time 300 \
        --output "$staging_dir/$archive.part" "https://www.busybox.net/downloads/$archive"
    mv -- "$staging_dir/$archive.part" "$reference_dir/$archive"
else
    printf 'Using cached archive: %s\n' "$reference_dir/$archive"
fi
python3 - "$reference_dir" "$staging_dir" "$project_dir/scripts" <<'PY'
from pathlib import Path
import hashlib,json,re,runpy,sys
from datetime import datetime,timezone
ref,stage,scripts=map(Path,sys.argv[1:]);name='busybox-1.37.0';archive=ref/(name+'.tar.bz2')
expected='3311dff32e746499f4df0d5df04d7eb396382d7e108bb9250e7b519b837043a4'
actual=hashlib.sha256(archive.read_bytes()).hexdigest()
if actual!=expected:raise SystemExit('BusyBox archive SHA256 mismatch; not extracted: '+str(archive))
links=runpy.run_path(str(scripts/'buildroot-archive.py'))['unpack'](archive,stage,name)
source=stage/name;text=(source/'Makefile').read_text()
for key,value in [('VERSION','1'),('PATCHLEVEL','37'),('SUBLEVEL','0')]:
    if not re.search(r'^'+key+r'\s*=\s*'+value+r'\s*$',text,re.M):raise SystemExit('Unexpected BusyBox version')
meta=dict(version='1.37.0',url='https://www.busybox.net/downloads/'+archive.name,archive_sha256=actual,hash_source='pinned Buildroot2025.02.19 package/busybox/busybox.hash',retrieved_utc=datetime.now(timezone.utc).isoformat(),compiled=False,board_accessed=False)
(stage/'source.json').write_text(json.dumps(meta,indent=2)+'\n')
(stage/'source-links.json').write_text(json.dumps(links,indent=2)+'\n')
with (stage/'source-SHA256SUMS').open('w') as out:
    for p in sorted(source.rglob('*')):
        if not p.is_symlink() and p.is_file():out.write(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(source)}\n')
print('PASS: BusyBox1.37.0 archive matches pinned Buildroot SHA256:',actual)
PY
mv -- "$staging_dir/source.json" "$reference_dir/$name-source.json"
mv -- "$staging_dir/source-links.json" "$reference_dir/$name-source-links.json"
mv -- "$staging_dir/source-SHA256SUMS" "$reference_dir/$name-source-SHA256SUMS"
mv -- "$staging_dir/$name" "$source_dir"
printf 'READY: BusyBox source at %s; not built, no board accessed.\n' "$source_dir"
