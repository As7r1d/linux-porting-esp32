#!/bin/bash
set -euo pipefail
work_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
build_dir="$work_dir/build"
python3 - "$build_dir/.config" <<'PY'
from pathlib import Path
import sys
lines=set(Path(sys.argv[1]).read_text().splitlines())
for key in ['NOMMU','STATIC','HUSH','SH_IS_HUSH','HUSH_INTERACTIVE','HUSH_LOOPS','HUSH_PRINTF','FEATURE_SH_MATH']:
    if 'CONFIG_'+key+'=y' not in lines:raise SystemExit('Missing BusyBox option: '+key)
for key in ['ASH','HUSH_JOB','HUSH_TICK','FEATURE_EDITING','FEATURE_SUID']:
    if 'CONFIG_'+key+'=y' in lines:raise SystemExit('Unexpected BusyBox option: '+key)
PY
