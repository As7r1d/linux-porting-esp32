#!/usr/bin/env bash
# Host build only. No serial access, downloads, installation or board operations.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

tool_prefix=${CROSS_COMPILE:-riscv64-unknown-elf-}
build_python=${C5_PYTHON:-python3}
if ! "$build_python" -B -c 'import esptool' >/dev/null 2>&1; then
    if [[ -z ${C5_PYTHON:-} && -x "$HOME/esp32-env/bin/python" ]]; then
        build_python="$HOME/esp32-env/bin/python"
    else
        printf 'esptool is missing from %s; activate esp32-env or set C5_PYTHON.\n' "$build_python" >&2
        exit 1
    fi
fi
mkdir -p build
project_dir=$(cd ../.. && pwd)
"$build_python" -B - "$project_dir" <<'PAYLOAD'
from pathlib import Path
import hashlib,sys
root=Path(sys.argv[1])/'kernel'
for line in (root/'build/SHA256SUMS').read_text().splitlines():
    expected,name=line.split(maxsplit=1)
    if hashlib.sha256((root/name).read_bytes()).hexdigest()!=expected:
        raise SystemExit('Kernel manifest mismatch: '+name)
if 'CONFIG_ESP32C5_SHELL_CHECKPOINT=y' not in (root/'build/.config').read_text() or 'C5 SHELL CHECKPOINT' not in (root/'linux-6.12/arch/riscv/kernel/c5-libc.c').read_text():
    raise SystemExit('Kernel intentional checkpoint missing')
PAYLOAD
cp "$project_dir/kernel/build/arch/riscv/boot/Image" build/Image
"$build_python" -B - <<'PY'
from pathlib import Path
import re
import subprocess
import hashlib
ref = Path('../../reference/esp-idf-v5.5.1')
commit = subprocess.check_output(['git', '-C', str(ref), 'rev-parse', 'HEAD'], text=True).strip()
if commit != 'fcae32885b0296b32044cb99ecbdc50d98dddb83':
    raise SystemExit('Unexpected PSRAM reference commit')
ld = (ref / 'components/esp_rom/esp32c5/ld/esp32c5.rom.ld').read_text()
for name, addr in [('esp_rom_spi_cmd_config', 0x40000114),
                   ('esp_rom_spi_cmd_start', 0x40000118),
                   ('esp_rom_spi_set_op_mode', 0x4000011c),
                   ('ROM_Boot_Cache_Init', 0x40000644),
                   ('Cache_Disable_Cache', 0x400006ac),
                   ('Cache_Enable_Cache', 0x400006b0),
                   ('Cache_WriteBack_Addr', 0x40000658),
                   ('Cache_Invalidate_Addr', 0x40000650),
                   ('esp_rom_spiflash_select_qio_pins', 0x40000178)]:
    match = re.search(r'\b' + name + r'\s*=\s*(0x[0-9a-fA-F]+);', ld)
    if not match or int(match[1], 16) != addr:
        raise SystemExit(f'ROM symbol mismatch: {name}')
paths = [
    'components/riscv/include/riscv/csr.h',
    'components/esp_hw_support/port/esp32c5/cpu_region_protect.c',
    'components/hal/esp32c5/include/hal/mmu_ll.h',
    'components/hal/esp32c5/include/hal/cache_ll.h',
    'components/soc/esp32c5/include/soc/ext_mem_defs.h',
    'components/esp_rom/esp32c5/include/esp32c5/rom/cache.h',
    'components/esp_rom/esp32c5/ld/esp32c5.rom.ld',
    'components/esp_rom/esp32c5/ld/esp32c5.rom.version.ld',
    'components/esp_rom/esp32c5/include/esp32c5/rom/opi_flash.h',
    'components/esp_rom/include/esp_rom_spiflash.h',
    'components/esp_psram/device/esp_psram_impl_ap_quad.c',
    'components/esp_psram/device/esp_quad_psram_defs_ap.h',
    'components/hal/esp32c5/include/hal/psram_ctrlr_ll.h',
    'components/soc/esp32c5/include/soc/spi_pins.h',
    'components/soc/esp32c5/register/soc/spi1_mem_reg.h',
    'components/soc/esp32c5/register/soc/spi_mem_c_reg.h',
    'components/soc/esp32c5/register/soc/io_mux_struct.h',
    'components/soc/esp32c5/ld/esp32c5.peripherals.ld',
    'components/soc/esp32c5/register/soc/io_mux_reg.h',
    'components/soc/esp32c5/register/soc/reg_base.h',
]
paths += ['components/hal/esp32c5/include/hal/usb_serial_jtag_ll.h', 'components/soc/esp32c5/register/soc/usb_serial_jtag_reg.h', 'components/soc/esp32c5/include/soc/clic_reg.h']
paths += ['components/esp_hw_support/port/esp32c5/systimer.c', 'components/hal/esp32c5/include/hal/systimer_ll.h', 'components/hal/esp32c5/include/hal/clk_tree_ll.h', 'components/soc/esp32c5/register/soc/pcr_reg.h', 'components/soc/esp32c5/register/soc/pcr_struct.h']
with Path('build/reference-SHA256SUMS').open('w') as out:
    for name in paths:
        p = ref / name
        out.write(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p}\n')
print('PASS: pinned C5 reference commit and ROM helper addresses')
PY
