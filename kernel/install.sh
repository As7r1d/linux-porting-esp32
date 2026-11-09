#!/usr/bin/env bash
# Install the canonical C5 platform sources after applying the integration patch.
set -euo pipefail
kernel_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
target=${1:?Usage: bash kernel/install.sh LINUX_SOURCE}
while IFS= read -r -d '' file; do
    relative=${file#"$kernel_dir/platform/"}
    install -D -m 644 "$file" "$target/$relative"
done < <(find "$kernel_dir/platform" -type f -print0)
install -D -m 644 "$kernel_dir/flash-service/abi.h" \
    "$target/drivers/mtd/devices/c5-flash-abi.h"
