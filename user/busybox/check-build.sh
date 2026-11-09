#!/bin/bash
set -euo pipefail
work_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
python3 - "$work_dir" <<'PY'
from pathlib import Path
import struct,hashlib,json,sys
root=Path(sys.argv[1]);binary=(root/'build/busybox').read_bytes();h=struct.unpack('>4s15I',binary[:64])
if h[0]!=b'bFLT' or h[1]!=4 or h[6]!=32768 or not h[8] or h[7]+4*h[8]>len(binary) or h[9]&~3:raise SystemExit('Unexpected BusyBox bFLT')
(root/'build/report.json').write_text(json.dumps(dict(version='1.37.0',shell='hush',flat_header=list(h[1:]),hardware_tested=False),indent=2)+'\n')
with (root/'build/SHA256SUMS').open('w') as out:
    for name in ['../../Makefile','verify-source.sh','configure.sh','check-config.sh','check-build.sh','minimal.config','build.sh','build/.config','build/busybox','build/busybox.gdb','build/report.json']:
        out.write(f'{hashlib.sha256((root/name).read_bytes()).hexdigest()}  {name}\n')
print('PASS: real BusyBox hush built as static NOMMU bFLT; no board accessed')
PY
