#!/usr/bin/env bash
# Download pinned-tag Espressif reference sources only. No SDK installation,
# submodule update, firmware build, serial access, or board operation.
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
reference_dir="$project_dir/reference/esp-idf-v5.5.1"
mkdir -p "$project_dir/reference" "$project_dir/logs"
if [[ -e "$reference_dir" ]]; then
    printf 'Verifying existing reference checkout without downloading: %s\n' "$reference_dir"
else
    git clone --depth 1 --branch v5.5.1 --filter=blob:none --sparse \
        https://github.com/espressif/esp-idf.git "$reference_dir"
    git -C "$reference_dir" sparse-checkout set \
    components/esp_psram \
    components/hal \
    components/soc/esp32c5 \
    components/soc/include \
    components/soc/riscv \
    components/esp_rom/esp32c5 \
    components/esp_rom/include \
    components/esp_hw_support \
    components/esp_mm \
    components/bootloader_support
fi

commit=$(git -C "$reference_dir" rev-parse HEAD)
[[ $(git -C "$reference_dir" describe --tags --exact-match HEAD) == v5.5.1 ]]
[[ "$commit" == fcae32885b0296b32044cb99ecbdc50d98dddb83 ]]
test -f "$reference_dir/components/esp_psram/device/esp_psram_impl_ap_quad.c"
test -f "$reference_dir/components/soc/esp32c5/include/soc/spi_mem_reg.h"
test -f "$reference_dir/components/soc/esp32c5/register/soc/spi1_mem_reg.h"
test -f "$reference_dir/components/esp_rom/esp32c5/ld/esp32c5.rom.ld"
manifest="$project_dir/reference/esp-idf-v5.5.1-source.txt"
{
    printf 'URL: https://github.com/espressif/esp-idf.git\n'
    printf 'Tag: v5.5.1\nCommit: %s\n' "$commit"
    printf 'Purpose: source audit for standalone C5 RAM-only PSRAM diagnostic\n'
    printf 'No SDK installation or submodule initialization performed.\n'
    printf '\nSparse directories:\n'
    git -C "$reference_dir" sparse-checkout list
} > "$manifest"
cat "$manifest"
printf '\nPASS: reference source download complete; board not accessed.\n'
printf 'Source: %s\n' "$reference_dir"
