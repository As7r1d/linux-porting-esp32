.DEFAULT_GOAL := all
CROSS_COMPILE ?= riscv64-unknown-elf-
PYTHON ?= python3
CC := $(CROSS_COMPILE)gcc
CFLAGS := -march=rv32imac_zicsr_zifencei -mabi=ilp32 -Os -g
CFLAGS += -ffreestanding -fno-builtin -fno-pic -fno-pie -fno-stack-protector
CFLAGS += -fno-unwind-tables -fno-asynchronous-unwind-tables -msmall-data-limit=0 -mno-relax
CFLAGS += -Wall -Wextra -Werror -nostdlib -nostartfiles
LDFLAGS := -Wl,--build-id=none,--no-relax,-Map=build/linux-shell.map -T memory.ld -T rom-functions.ld
.PHONY: all
all:
	$(CC) $(CFLAGS) $(LDFLAGS) start.S main.c -lgcc -o build/linux-shell.elf
	$(CROSS_COMPILE)readelf -h -l -A build/linux-shell.elf > build/linux-shell.readelf.txt
	$(CROSS_COMPILE)objdump -d build/linux-shell.elf > build/linux-shell.disassembly.txt
	$(CROSS_COMPILE)nm -n build/linux-shell.elf > build/linux-shell.symbols.txt
	$(PYTHON) -B -m esptool --chip esp32c5 elf2image --flash-size 8MB -o build/linux-shell.bin build/linux-shell.elf
	$(PYTHON) -B -m esptool --chip esp32c5 image-info build/linux-shell.bin > build/linux-shell.image-info.txt
	bash check-build.sh
