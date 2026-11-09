.DEFAULT_GOAL := all
CROSS_COMPILE ?= riscv64-unknown-elf-
PYTHON ?= python3
CC := $(CROSS_COMPILE)gcc
CFLAGS := -march=rv32imac_zicsr_zifencei -mabi=ilp32 -Os -g
CFLAGS += -ffreestanding -fno-builtin -fno-pic -fno-pie -fno-stack-protector
CFLAGS += -fno-unwind-tables -fno-asynchronous-unwind-tables -msmall-data-limit=0 -mno-relax
CFLAGS += -ffunction-sections -fdata-sections -Wall -Wextra -Werror -nostdlib -nostartfiles
LDFLAGS := -Wl,--build-id=none,--no-relax,--gc-sections,-Map=build/bootloader.map
LDFLAGS += -T memory.ld -T rom-functions.ld
.PHONY: all prepare
all: prepare
	$(CC) $(CFLAGS) $(LDFLAGS) start.S main.c -lgcc -o build/bootloader.elf
	$(PYTHON) -B -m esptool --chip esp32c5 elf2image --flash-mode dio --flash-freq 20m --flash-size 8MB -o build/bootloader.bin build/bootloader.elf
prepare:
	$(PYTHON) -B build.py --prepare
