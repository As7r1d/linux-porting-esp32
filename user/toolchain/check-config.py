#!/usr/bin/env python3
"""Reject an accidental MMU/FPU/dynamic/threaded/QEMU-kernel toolchain."""
from pathlib import Path
import json,sys
root=Path(__file__).resolve().parent
output=root/'output'
lines=set((output/'.config').read_text().splitlines())
required=['BR2_riscv=y','BR2_riscv_custom=y','BR2_RISCV_32=y','BR2_RISCV_ISA_RVM=y','BR2_RISCV_ISA_RVA=y','BR2_RISCV_ISA_RVC=y','BR2_RISCV_ABI_ILP32=y','BR2_BINFMT_FLAT=y','BR2_TOOLCHAIN_BUILDROOT_UCLIBC=y','BR2_GCC_VERSION="13.4.0"','BR2_PTHREADS_NONE=y','BR2_STATIC_LIBS=y','BR2_DEFAULT_KERNEL_HEADERS="6.12"','BR2_DOWNLOAD_FORCE_CHECK_HASHES=y']
for line in required:
    if line not in lines:raise SystemExit('Required toolchain setting absent: '+line)
for name in ['BR2_USE_MMU','BR2_RISCV_USE_MMU','BR2_RISCV_64','BR2_RISCV_ISA_RVF','BR2_RISCV_ISA_RVD','BR2_RISCV_ISA_RVV','BR2_TOOLCHAIN_HAS_THREADS','BR2_TOOLCHAIN_HAS_SSP','BR2_SHARED_LIBS','BR2_SHARED_STATIC_LIBS','BR2_LINUX_KERNEL','BR2_PACKAGE_HOST_QEMU','BR2_PACKAGE_BUSYBOX']:
    if name+'=y' in lines:raise SystemExit('Unexpected enabled setting: '+name)
print('PASS: RV32IMAC/ILP32, NOMMU, static bFLT/uClibc, GCC13.4, headers6.12; no threads/FPU/QEMU kernel')
