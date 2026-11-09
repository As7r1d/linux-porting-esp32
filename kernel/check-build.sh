#!/bin/bash
set -euo pipefail
c5_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_dir=$(cd -- "$c5_dir/.." && pwd)
build_dir="$c5_dir/build"
python3 -B "$c5_dir/test_host.py"
(cd "$c5_dir" && sha256sum ../Makefile configure.sh check-config.sh check-build.sh ../linux.config build.sh ../linux.patch source-hashes.json prepare.py test_host.py build/.config build/vmlinux build/arch/riscv/boot/Image flash-service/build/SHA256SUMS flash-service/build/service.h > build/SHA256SUMS)
printf 'C5 PID1 checkpoint built. Host checks only; new kernel/user hardware execution untested.\n'
