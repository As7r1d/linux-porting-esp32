#!/usr/bin/env bash
# Prepare upstream Linux with a small integration patch and explicit C5 sources.
set -euo pipefail
kernel_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_dir=$(cd -- "$kernel_dir/.." && pwd)
reference="$project_dir/reference/linux-6.12"
target="$kernel_dir/linux-6.12"
if [[ -e "$target" ]]; then
    # Preserve unexpected work-tree edits rather than silently overwrite them.
    (cd "$target" && sha256sum --check --status "$kernel_dir/port.sha256")
    bash "$kernel_dir/install.sh" "$target"
    (cd "$target" && sha256sum --check --status "$kernel_dir/port.sha256")
    exit 0
fi
(cd "$reference" && sha256sum --check --status "$kernel_dir/pristine.sha256")
staging=$(mktemp -d "$kernel_dir/.prepare.XXXXXXXX")
trap 'rm -rf -- "$staging"' EXIT
cp -a "$reference/." "$staging/"
patch --batch --fuzz=0 -d "$staging" -p1 -i "$project_dir/linux.patch"
bash "$kernel_dir/install.sh" "$staging"
(cd "$staging" && sha256sum --check --status "$kernel_dir/port.sha256")
[[ ! -e "$target" ]] || { echo 'Another preparation created the target; preserving it.' >&2; exit 1; }
mv -- "$staging" "$target"
printf 'Prepared Linux integration patch and C5 platform sources. No board accessed.\n'
