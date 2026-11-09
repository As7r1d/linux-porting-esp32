#!/usr/bin/env bash
# Read-only board preflight. Resets to ROM download mode; leaves it there.
# No Flash write/erase, eFuse burn, or automatic flashing step.
set -euo pipefail
work_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_dir=$(cd -- "$work_dir/.." && pwd)
port=${1:-/dev/ttyACM0}
python_bin=${C5_PYTHON:-python3}
"$python_bin" -B -c 'import esptool; assert esptool.__version__ == "5.6.0"'
mkdir -p "$project_dir/logs"
backup_dir=$(mktemp -d "$project_dir/logs/flash-boot-preflight-XXXXXXXX")
trap 'printf "Preflight FAILED: complete backup/review not established. Preserve %s; do not flash.\n" "$backup_dir" >&2' ERR
printf 'Read-only preflight: ROM reset, security info, SRAM read stub, full8MiB Flash backup.\nBackup directory: %s\n' "$backup_dir"
"$python_bin" -B -m esptool --chip esp32c5 --port "$port" --no-stub --after no-reset get-security-info 2>&1 | tee "$backup_dir/security.txt"
"$python_bin" -B -m esptool --chip esp32c5 --port "$port" --after no-reset read-flash --flash-size 8MB 0 0x800000 "$backup_dir/flash-8MiB.bin" 2>&1 | tee "$backup_dir/read.txt"
"$python_bin" -B "$work_dir/review.py" "$backup_dir/flash-8MiB.bin" --report "$backup_dir/review.json" 2>&1 | tee "$backup_dir/review.txt"
printf 'READ-ONLY COMPLETE: no Flash/eFuse write or erase. Board remains in download mode.\nShare security.txt and review.txt before a write procedure is prepared.\n'
