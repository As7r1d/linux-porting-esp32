#!/usr/bin/env bash
# Host source download only; no board, Flash, tool installation or kernel edits.
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
version=6.12
archive="linux-$version.tar.xz"
base=https://cdn.kernel.org/pub/linux/kernel/v6.x
reference_dir="$project_dir/reference"
source_dir="$reference_dir/linux-$version"
if [[ -e "$source_dir" ]]; then
    printf 'Source directory already exists; preserving it: %s\n' "$source_dir"
    exit 1
fi
mkdir -p "$reference_dir"
staging_dir=$(mktemp -d "$reference_dir/.linux-download.XXXXXXXX")
trap 'rm -rf -- "$staging_dir"' EXIT
curl --fail --location --retry 2 --connect-timeout 20 --output "$staging_dir/$archive" "$base/$archive"
curl --fail --location --retry 2 --connect-timeout 20 --output "$staging_dir/sha256sums.asc" "$base/sha256sums.asc"
python3 - "$staging_dir" "$archive" <<'PY'
import hashlib,re,sys
from pathlib import Path
root=Path(sys.argv[1]); name=sys.argv[2]
entries=[]
for line in (root/'sha256sums.asc').read_text().splitlines():
    match=re.fullmatch(r'([0-9a-f]{64})\s+\*?'+re.escape(name),line)
    if match: entries.append(match[1])
if len(entries)!=1: raise SystemExit('Expected exactly one official checksum entry')
actual=hashlib.sha256((root/name).read_bytes()).hexdigest()
if actual!=entries[0]: raise SystemExit('Archive checksum mismatch; not extracted')
print('PASS: archive matches kernel.org SHA256:',actual)
print('Checksum retrieved over HTTPS; OpenPGP signature is not verified by this script.')
(root/'archive-SHA256SUMS').write_text(f'{actual}  {name}\n')
PY
tar -xJf "$staging_dir/$archive" -C "$staging_dir" --no-same-owner
python3 - "$staging_dir/linux-$version/Makefile" <<'PY'
from pathlib import Path
import re,sys
s=Path(sys.argv[1]).read_text()
for key,value in [('VERSION','6'),('PATCHLEVEL','12'),('SUBLEVEL','0')]:
    if not re.search(r'^'+key+r'\s*=\s*'+value+r'\s*$',s,re.M):
        raise SystemExit('Unexpected kernel version')
PY
mv -- "$staging_dir/linux-$version" "$source_dir"
cp -- "$staging_dir/archive-SHA256SUMS" "$reference_dir/linux-$version-archive-SHA256SUMS"
cp -- "$staging_dir/sha256sums.asc" "$reference_dir/linux-$version-sha256sums.asc"
{
    printf 'Source: %s/%s\nVersion: %s\n' "$base" "$archive" "$version"
    printf 'Purpose: isolated RV32 M-mode NOMMU baseline; not bootable on C5 yet.\n'
    printf 'Retrieved UTC: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    cat "$reference_dir/linux-$version-archive-SHA256SUMS"
} > "$reference_dir/linux-$version-source.txt"
printf '\nPASS: Linux %s source ready at %s; board not accessed.\n' "$version" "$source_dir"
