#!/bin/bash
set -euo pipefail
c5_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_dir=$(cd -- "$c5_dir/.." && pwd)
build_dir="$c5_dir/build"
(cd "$c5_dir" && sha256sum ../Makefile configure.sh check-config.sh check-build.sh ../linux.config build.sh ../linux.patch pristine.sha256 port.sha256 prepare.sh install.sh build/.config build/vmlinux build/arch/riscv/boot/Image flash-service/build/SHA256SUMS flash-service/build/service.h > build/SHA256SUMS)
(cd "$c5_dir" && find platform -type f -print0 | sort -z | xargs -0 sha256sum >> build/SHA256SUMS)
printf 'C5 PID1 checkpoint built. Host checks only; new kernel/user hardware execution untested.\n'
