#!/usr/bin/env bash
# Assemble files only; standard mkfs.cramfs does filesystem serialization.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
for tool in mkfs.cramfs fsck.cramfs fakeroot; do
    command -v "$tool" >/dev/null || { echo "Missing host tool: $tool" >&2; exit 1; }
done
(cd ../busybox && sha256sum --check --status build/SHA256SUMS)
mkdir -p build
# New staging tree each time: deleted applets must not survive an incremental build.
stage=$(mktemp -d "$PWD/build/stage.XXXXXXXX")
trap 'rm -rf -- "$stage"' EXIT
mkdir -p "$stage"/{bin,dev,proc,sys,etc,tmp,run,data,home}
install -m 755 ../busybox/build/busybox "$stage/bin/busybox"
for applet in hush sh cat ls uname mount mkdir sync dd hexdump; do
    ln -s busybox "$stage/bin/$applet"
done
install -m 755 init "$stage/init"
cp -a overlay/. "$stage/"
rm -rf -- build/root
mv -- "$stage" build/root
