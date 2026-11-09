#!/bin/bash
set -euo pipefail
c5_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_dir=$(cd -- "$c5_dir/.." && pwd)
build_dir="$c5_dir/build"
python3 - "$build_dir/.config" <<'PY'
import sys
from pathlib import Path
s=Path(sys.argv[1]).read_text().splitlines()
for line in ['CONFIG_ARCH_RV32I=y','CONFIG_RISCV_M_MODE=y','# CONFIG_MMU is not set',
             '# CONFIG_SMP is not set','# CONFIG_FPU is not set','CONFIG_PRINTK=y','CONFIG_OF=y','CONFIG_ESP32C5_BOOTSTRAP=y','CONFIG_PAGE_OFFSET=0x42000000','CONFIG_SERIAL_EARLYCON=y','CONFIG_SERIAL_CORE_CONSOLE=y']:
    if line not in s: raise SystemExit('Required config missing: '+line)
for line in ['CONFIG_MTD=y','CONFIG_MTD_ROM=y','CONFIG_MTD_PHYSMAP=y',
             'CONFIG_MTD_PHYSMAP_OF=y','CONFIG_CRAMFS=y','CONFIG_CRAMFS_MTD=y',
             'CONFIG_INITRAMFS_SOURCE=""','CONFIG_DEVTMPFS=y','CONFIG_DEVTMPFS_MOUNT=y','CONFIG_JFFS2_FS=y']:
    if line not in s: raise SystemExit('Storage config missing: '+line)
PY
