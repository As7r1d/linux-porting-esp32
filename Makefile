# Main build recipes live here; Python helpers only prepare/package/check data.
.DEFAULT_GOAL := help
ROOT := $(abspath .)
PYTHON ?= python3
CROSS_COMPILE ?= riscv64-unknown-elf-
JOBS ?= 4
KERNEL_SOURCE := $(ROOT)/kernel/linux-6.12
KERNEL_OUTPUT := $(ROOT)/kernel/build
BR_SOURCE := $(ROOT)/reference/buildroot-2025.02.19
TC_OUTPUT := $(ROOT)/user/toolchain/output
BB_SOURCE := $(ROOT)/reference/busybox-1.37.0
BB_OUTPUT := $(ROOT)/user/busybox/build
USER_CROSS := $(TC_OUTPUT)/host/bin/riscv32-buildroot-linux-uclibc-
export C5_PYTHON := $(PYTHON)
STORAGE_WRITE ?= 0
export STORAGE_WRITE
.PHONY: help fetch prepare toolchain busybox rootfs kernel linux dtb ram-loader bootloader flash-image all check public-check source
help:
	@printf '%s\n' 'make fetch       Download pinned upstream sources' 'make prepare     Apply kernel patch to fresh source copy if absent' 'make toolchain   Build RV32 Linux GCC/libc with Buildroot' 'make busybox     Configure and compile BusyBox' 'make rootfs      Stage files and build independent cramfs' 'make kernel      Build rootfs and compile Linux (alias: make linux)' 'make dtb         Compile device tree' 'make ram-loader  Compile shared loader/DTB (legacy uploader cannot load rootfs)' 'make bootloader  Compile Flash bootloader' 'make flash-image Package five Flash payloads (requires kernel/DTB)' 'make all         Run stages in order after make fetch' 'make check       Host checks only; no hardware qualification' 'Build targets never open ports or write Flash.'
fetch:
	@test -d reference/linux-6.12 || bash scripts/fetch-linux-reference.sh
	bash scripts/fetch-buildroot-reference.sh
	bash scripts/fetch-busybox-reference.sh
	bash scripts/fetch-psram-reference.sh
	bash scripts/fetch-cpu-reference.sh
prepare:
	@test -d kernel/linux-6.12 || $(PYTHON) -B kernel/prepare.py
toolchain:
	bash user/toolchain/configure.sh
	$(MAKE) -C $(BR_SOURCE) O=$(TC_OUTPUT) BR2_DEFCONFIG=$(TC_OUTPUT)/c5_defconfig defconfig
	$(PYTHON) -B user/toolchain/check-config.py
	$(MAKE) -C $(BR_SOURCE) O=$(TC_OUTPUT) dependencies
	$(MAKE) -C $(BR_SOURCE) O=$(TC_OUTPUT) -j$(JOBS) toolchain
	$(PYTHON) -B user/toolchain/smoke.py
busybox:
	bash user/busybox/verify-source.sh
	$(MAKE) -C $(BB_SOURCE) O=$(BB_OUTPUT) ARCH=riscv CROSS_COMPILE=$(USER_CROSS) allnoconfig
	bash user/busybox/configure.sh
	$(MAKE) -C $(BB_SOURCE) O=$(BB_OUTPUT) ARCH=riscv CROSS_COMPILE=$(USER_CROSS) oldconfig < $(BB_OUTPUT)/config-answers.txt
	bash user/busybox/check-config.sh
	$(MAKE) -C $(BB_SOURCE) O=$(BB_OUTPUT) ARCH=riscv CROSS_COMPILE=$(USER_CROSS) -j$(JOBS) CFLAGS='-Os -fPIC -D_LARGEFILE_SOURCE -D_LARGEFILE64_SOURCE -D_FILE_OFFSET_BITS=64' EXTRA_LDFLAGS='-Wl,-elf2flt=-r -Wl,-elf2flt=-s32768' SKIP_STRIP=y
	$(USER_CROSS)flthdr -s 32768 $(BB_OUTPUT)/busybox
	cp $(BB_OUTPUT)/busybox_unstripped.gdb $(BB_OUTPUT)/busybox.gdb
	bash user/busybox/check-build.sh
rootfs:
	bash user/rootfs/stage.sh
	fakeroot -- bash user/rootfs/image.sh
	$(PYTHON) -B user/rootfs/test_host.py
flash-service:
	$(MAKE) -C kernel/flash-service PYTHON=$(PYTHON) CROSS_COMPILE=$(CROSS_COMPILE)
kernel: prepare rootfs flash-service
	bash kernel/configure.sh
	$(MAKE) -C $(KERNEL_SOURCE) O=$(KERNEL_OUTPUT) ARCH=riscv CROSS_COMPILE=$(CROSS_COMPILE) KCONFIG_ALLCONFIG=$(KERNEL_OUTPUT)/minimal.config allnoconfig
	bash kernel/check-config.sh
	$(MAKE) -C $(KERNEL_SOURCE) O=$(KERNEL_OUTPUT) ARCH=riscv CROSS_COMPILE=$(CROSS_COMPILE) -j$(JOBS) vmlinux Image
	$(CROSS_COMPILE)size $(KERNEL_OUTPUT)/vmlinux > $(KERNEL_OUTPUT)/size.txt
	$(CROSS_COMPILE)readelf -h -l $(KERNEL_OUTPUT)/vmlinux > $(KERNEL_OUTPUT)/readelf.txt
	$(CROSS_COMPILE)nm -n $(KERNEL_OUTPUT)/vmlinux > $(KERNEL_OUTPUT)/symbols.txt
	bash kernel/check-build.sh
linux: kernel
dtb:
	mkdir -p bootloader/psram/build
	$(KERNEL_OUTPUT)/scripts/dtc/dtc -I dts -O dtb -o bootloader/psram/build/c5-early.dtb esp32c5.dts
ram-loader: dtb
	bash bootloader/psram/prepare.sh
	$(MAKE) -C bootloader/psram PYTHON=$(PYTHON) CROSS_COMPILE=$(CROSS_COMPILE)
bootloader:
	$(MAKE) -C bootloader PYTHON=$(PYTHON) CROSS_COMPILE=$(CROSS_COMPILE)
flash-image: bootloader
	$(PYTHON) -B bootloader/build.py
# Sequential submakes keep source/build dependencies ordered even with make -j.
all:
	$(MAKE) toolchain
	$(MAKE) busybox
	$(MAKE) kernel
	$(MAKE) ram-loader
	$(MAKE) flash-image
check:
	$(MAKE) -C kernel/flash-service check PYTHON=$(PYTHON)
	$(PYTHON) -B user/rootfs/test_host.py
	$(PYTHON) -B kernel/test_host.py
	$(PYTHON) -B bootloader/psram/test_host.py
	$(PYTHON) -B bootloader/test_host.py

public-check:
	$(PYTHON) -B scripts/public-source.py check
source: public-check
	$(PYTHON) -B scripts/public-source.py export
