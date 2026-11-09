#!/bin/bash
set -euo pipefail
work_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
build_dir="$work_dir/build"
python3 - "$work_dir/minimal.config" "$build_dir/.config" <<'MERGE'
from pathlib import Path
import re,sys
seed=Path(sys.argv[1]).read_text().splitlines();out=Path(sys.argv[2])
values={}
for line in seed:
    m=re.match(r'(?:# )?(CONFIG_\w+)(?:=| is not set)',line)
    if m:values[m[1]]=line
lines=[]
for line in out.read_text().splitlines():
    m=re.match(r'(?:# )?(CONFIG_\w+)(?:=| is not set)',line)
    lines.append(values.pop(m[1],line) if m else line)
lines.extend(values.values());out.write_text('\n'.join(lines)+'\n')
MERGE
python3 - "$build_dir/config-answers.txt" <<'ANSWERS'
from pathlib import Path
import sys
Path(sys.argv[1]).write_text('\n'*10000)
ANSWERS
