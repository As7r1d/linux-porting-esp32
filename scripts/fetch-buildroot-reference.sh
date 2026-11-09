#!/usr/bin/env bash
# Download pinned host-side source only; no board access or system installation.
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
version=2025.02.19
reference_dir="$project_dir/reference"
source_dir="$reference_dir/buildroot-$version"
archive="buildroot-$version.tar.xz"
base=https://buildroot.org/downloads
if [[ -e "$source_dir" ]]; then
    printf 'Source already exists; preserving it: %s\n' "$source_dir"
    exit 0
fi
mkdir -p "$reference_dir"
staging_dir=$(mktemp -d "$reference_dir/.buildroot-download.XXXXXXXX")
trap 'rm -rf -- "$staging_dir"' EXIT
printf 'Fetching Buildroot %s source only; no compilation or board access.\n' "$version"
# Cache completed downloads outside extraction staging, including on failure.
for name in "$archive" "$archive.sign"; do
    if [[ -s "$reference_dir/$name" ]]; then
        printf 'Using cached download: %s\n' "$reference_dir/$name"
    else
        curl --fail --location --proto '=https' --retry 2 --connect-timeout 20 --max-time 300 \
            --output "$staging_dir/$name.part" "$base/$name"
        mv -- "$staging_dir/$name.part" "$reference_dir/$name"
    fi
done
python3 - "$staging_dir" "$version" "$reference_dir" "$project_dir/scripts" <<'PYEXTRACT'
from pathlib import Path
import hashlib, json, re, sys, runpy
from datetime import datetime, timezone
unpack=runpy.run_path(str(Path(sys.argv[4])/'buildroot-archive.py'))['unpack']
stage=Path(sys.argv[1]);version=sys.argv[2];name=f'buildroot-{version}'
cache=Path(sys.argv[3]);archive=cache/(name+'.tar.xz');sig=cache/(name+'.tar.xz.sign')
if not 0<sig.stat().st_size<65536:raise SystemExit('Unexpected detached signature size')
links=unpack(archive,stage,name)
source=stage/name
makefile=(source/'Makefile').read_text()
if not re.search(r'^(?:export\s+)?BR2_VERSION\s*:?=\s*'+re.escape(version)+r'\s*$',makefile,re.M):
    raise SystemExit('Unexpected Buildroot version')
configs=sorted(p.name for p in (source/'configs').glob('*riscv32*nommu*'))
if not configs:raise SystemExit('Expected RV32 NOMMU reference configuration absent')
metadata={
    'version':version,'url':f'https://buildroot.org/downloads/{archive.name}',
    'retrieved_utc':datetime.now(timezone.utc).isoformat(),
    'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
    'signature_sha256':hashlib.sha256(sig.read_bytes()).hexdigest(),
    'pgp_signature_verified':False,
    'integrity_note':'HTTPS download; local SHA256 recorded, not compared with an independently trusted checksum. Detached PGP signature saved but not verified.',
    'rv32_nommu_reference_configs':configs,'compiled':False,'board_accessed':False,
}
(stage/'source.json').write_text(json.dumps(metadata,indent=2)+'\n')
with (stage/'source-SHA256SUMS').open('w') as out:
    for p in sorted(source.rglob('*')):
        if not p.is_symlink() and p.is_file():
            out.write(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(source)}\n')
(stage/'source-links.json').write_text(json.dumps(links,indent=2)+'\n')
print('Version and RV32 NOMMU reference configuration checked:', ', '.join(configs))
print('Archive local SHA256:',metadata['archive_sha256'])
print('HTTPS source downloaded; detached PGP signature saved, NOT verified.')
PYEXTRACT
mv -- "$staging_dir/source-links.json" "$reference_dir/buildroot-$version-source-links.json"
mv -- "$staging_dir/source.json" "$reference_dir/buildroot-$version-source.json"
mv -- "$staging_dir/source-SHA256SUMS" "$reference_dir/buildroot-$version-source-SHA256SUMS"
mv -- "$staging_dir/buildroot-$version" "$source_dir"
printf '\nREADY: Buildroot source at %s; toolchain not built; board not accessed.\n' "$source_dir"
