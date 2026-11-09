# Linux on ESP32-C5

Experimental Linux 6.12 for ESP32-C5HR8 with 8 MiB PSRAM and 8 MiB external
Flash. RV32 M-mode, NOMMU, static uClibc/bFLT BusyBox hush.

Board-tested milestones: Flash boot, independent read-only cramfs root,
USB console, external programs, SRAM-mediated Flash reads, and a synced JFFS2
`/data` file surviving the operator's power-cycle procedure.

This is a diagnostic port: 32768 ticks / 4096 PID1 syscalls, intentional halt
on PID1 exit, no complete memory isolation. General warm restart, networking,
long-term operation, erase/GC stress and arbitrary power-failure recovery are
not qualified. The loader still stages rootfs in PSRAM; this is not Flash XIP.

## Source layout

```text
Makefile, build.sh       Host build entry points
linux.config            Kernel configuration seed
linux.patch             Upstream integration hooks and build changes
esp32c5.dts             Board description
kernel/platform/        Canonical C5 kernel C/assembly/header sources
kernel/                 Shell preparation, source checks, SRAM Flash service
bootloader/             SRAM Flash loader, linker script, board tools
bootloader/psram/       Shared low-level PSRAM bootstrap
user/toolchain/         Buildroot RV32 NOMMU toolchain configuration
user/busybox/           BusyBox configuration and checks
user/rootfs/            /init, overlay, standard cramfs packaging
scripts/                Pinned source fetching
tests/                  Flash/console and selected kernel regressions
```

The layout follows the STM32H7 reference's explicit Makefile/config/DTS/loader
separation. Filesystem/data separation and validation practices also draw on
the S3 references. C5 addresses, ROM format and runtime driver are platform-specific.

## Build

Use Linux, Python 3.12+, make, GCC/build dependencies, git, curl, patch,
archive tools, fakeroot, mkfs.cramfs/fsck.cramfs, and an RV32-capable
`riscv64-unknown-elf-` GCC/binutils. Buildroot checks further host dependencies.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install esptool==5.6.0 pyserial==3.5
make fetch
make prepare
make toolchain
make busybox
make kernel
make ram-loader
make flash-image
make check
```

Or `sh build.sh` to fetch and build in order. Default `STORAGE_WRITE=0` prohibits
runtime data writes. The previously tested writable variant uses
`make STORAGE_WRITE=1 kernel` followed by loader/image packaging. Build commands
never open a board port or flash automatically. See [BUILDING.md](BUILDING.md).

Read [board operations](docs/BOARD.md) before using the serial tools.
See [storage](docs/STORAGE.md), [validation](docs/VALIDATION.md) and
[third-party notices](NOTICE.md). Historical board results are not automatic
qualification of binaries rebuilt after this directory reorganization.
