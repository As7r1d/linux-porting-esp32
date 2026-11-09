# Build organization

The root Makefile contains the actual Buildroot, BusyBox and Linux recipes.
`build.sh` sequences fetching and building. `bootloader/Makefile` compiles,
links and converts the loader ELF with esptool. `memory.ld` controls SRAM
placement. `kernel/flash-service/Makefile` builds the separate SRAM service.
C5 kernel implementations live in kernel/platform/ and are copied into the
upstream tree by kernel/install.sh. linux.patch contains only integration hooks
and changes to existing upstream files. kernel/prepare.sh applies the patch,
installs the platform sources and checks kernel/port.sha256.

Python remains for metadata, validation and serial tooling; rootfs serialization
uses standard mkfs.cramfs under fakeroot.

Run the stages in README order. Individual targets require preceding artifacts.
`make kernel` also assembles rootfs and the SRAM service. `make ram-loader`
builds the DTB and shared bootstrap imported by the Flash loader; no live
RAM uploader is shipped. bootloader/console.py provides only the terminal and
ledger functions needed by the current Flash capture workflow.
`make flash-image` produces five payloads and a checked plan under
`bootloader/build/`. `make check` performs host checks, never hardware tests.

Set `JOBS=2` for lower compiler concurrency, `PYTHON=...` for a selected Python,
and `CROSS_COMPILE=...` for the bare-metal compiler. `STORAGE_WRITE=0` is the
default; `STORAGE_WRITE=1` changes the forced kernel command line and allows
JFFS2 /data mounting/writes at boot. Repackage after every kernel/config change.

Generated paths are ignored: reference/, kernel/linux-6.12, component build/
directories, user/toolchain/output, logs/, .local/ and public-output/.
Toolchain outputs are not promised relocatable; rebuild on a new machine.

Clean network/toolchain reproduction remains unqualified. Local host validation
reuses previously downloaded upstreams and an existing compiler; it rebuilds
BusyBox, rootfs, kernel and loaders in this layout. This scope is recorded
separately from earlier board-qualified artifacts.
