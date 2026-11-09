CROSS_COMPILE ?= riscv64-unknown-elf-
PYTHON ?= python3
CFLAGS = -march=rv32imac_zicsr_zifencei -mabi=ilp32 -Os -g -Wall -Wextra -Werror
CFLAGS += -ffreestanding -fno-builtin -fno-pic -fno-pie -fno-stack-protector -msmall-data-limit=0 -mno-relax
CFLAGS += -fno-unwind-tables -fno-asynchronous-unwind-tables -nostdlib -nostartfiles
.PHONY: all check
all: build/service.h check
build/service.elf: entry.S service.c abi.h memory.ld
	mkdir -p build
	$(CROSS_COMPILE)gcc $(CFLAGS) -Wl,--no-relax,--build-id=none,-T,memory.ld entry.S service.c -o $@
build/service.bin: build/service.elf
	$(CROSS_COMPILE)objcopy -O binary $< $@
build/service.h: build/service.bin
	$(PYTHON) -c 'from pathlib import Path; b=Path("$<").read_bytes(); Path("$@").write_text("static const unsigned char c5_service_image[] = {"+",".join(str(v) for v in b)+"};\n")'
check: build/service.h
	$(PYTHON) -B test_host.py
	sha256sum Makefile entry.S service.c abi.h memory.ld test_host.py build/service.elf build/service.bin build/service.h > build/SHA256SUMS
