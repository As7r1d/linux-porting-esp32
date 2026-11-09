#!/usr/bin/env bash
# Run inside one fakeroot session so device nodes and root ownership are encoded.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
[[ -n ${FAKEROOTKEY:-} ]] || { echo 'Run make rootfs (requires fakeroot).' >&2; exit 1; }
rm -f build/root/dev/console build/root/dev/null
mknod -m 600 build/root/dev/console c 5 1
mknod -m 666 build/root/dev/null c 1 3
# fakeroot reports untracked files as root-owned; no real chown is needed.
mkfs.cramfs -E -N little -n c5-rootfs build/root build/rootfs.cramfs
fsck.cramfs build/rootfs.cramfs
test "$(stat -c %s build/rootfs.cramfs)" -le $((0xf0000))
sha256sum stage.sh image.sh init build/rootfs.cramfs > build/SHA256SUMS
find overlay -type f -print0 | sort -z | xargs -0 sha256sum >> build/SHA256SUMS
