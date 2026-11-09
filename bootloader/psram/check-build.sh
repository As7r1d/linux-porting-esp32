#!/bin/bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
build_python=${C5_PYTHON:-python3}
tool_prefix=${CROSS_COMPILE:-riscv64-unknown-elf-}
"$build_python" -B - <<'PY'
from esptool.bin_image import LoadFirmwareImage
from pathlib import Path

image = LoadFirmwareImage('esp32c5', Path('build/linux-shell.bin').read_bytes())
image.verify()
if image.entrypoint != 0x40800000:
    raise SystemExit('Unexpected image entry point')
for segment in image.segments:
    end = segment.addr + len(segment.data)
    if not (0x40800000 <= segment.addr < end <= 0x40806000):
        raise SystemExit(f'Segment outside reserved code/data SRAM: {segment.addr:#x}..{end:#x}')
    print(f'SRAM segment: {segment.addr:#010x}..{end:#010x}, {len(segment.data)} bytes')
print('PASS: image entry and loaded segments are in reserved internal SRAM')
PY
"${tool_prefix}size" build/linux-shell.elf
sha256sum ../../Makefile Makefile prepare.sh check-build.sh start.S main.c memory.ld rom-functions.ld build.sh ../../esp32c5.dts build/Image build/c5-early.dtb build/linux-shell.elf build/linux-shell.bin > build/SHA256SUMS
cat build/reference-SHA256SUMS >> build/SHA256SUMS
printf 'Host build complete. No board was accessed. Image: %s/build/linux-shell.bin\n' "$PWD"
