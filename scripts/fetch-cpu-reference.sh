#!/usr/bin/env bash
# Host source download only: no SDK installation, serial access or firmware action.
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
reference_dir="$project_dir/reference/esp-idf-v5.5.1"
expected_commit=fcae32885b0296b32044cb99ecbdc50d98dddb83
actual_commit=$(git -C "$reference_dir" rev-parse HEAD)
if [[ "$actual_commit" != "$expected_commit" ]]; then
    printf 'Unexpected reference commit; stopped without changing checkout.\n' >&2
    exit 1
fi
if [[ -n $(git -C "$reference_dir" status --porcelain --untracked-files=no) ]]; then
    printf 'Reference has tracked modifications; stopped to preserve them.\n' >&2
    exit 1
fi
printf 'Adding only components/riscv to the existing pinned source checkout.\n'
git -C "$reference_dir" sparse-checkout add components/riscv
[[ $(git -C "$reference_dir" rev-parse HEAD) == "$expected_commit" ]]
for relative_path in components/riscv/include/riscv/csr.h components/riscv/include/riscv/rv_utils.h; do
    expected_blob=$(git -C "$reference_dir" rev-parse "HEAD:$relative_path")
    actual_blob=$(git -C "$reference_dir" hash-object "$relative_path")
    [[ "$actual_blob" == "$expected_blob" ]]
    printf 'Verified Git blob %s  %s\n' "$actual_blob" "$relative_path"
done
manifest="$project_dir/reference/esp-idf-v5.5.1-cpu-source.txt"
{
    printf 'URL: https://github.com/espressif/esp-idf.git\n'
    printf 'Commit: %s\n' "$expected_commit"
    printf 'Purpose: C5 PMA/PMP/CSR source audit; no hardware operation.\n'
    printf 'Retrieved UTC: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '\nSparse directories:\n'
    git -C "$reference_dir" sparse-checkout list
    printf '\nCPU header SHA-256:\n'
    (cd "$reference_dir" && sha256sum components/riscv/include/riscv/csr.h components/riscv/include/riscv/rv_utils.h)
} > "$manifest"
cat "$manifest"
printf '\nPASS: CPU reference headers verified; board not accessed.\n'
