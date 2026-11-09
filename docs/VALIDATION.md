# Validation scope

Board: ESP32-C5HR8, 8 MiB PSRAM, external 8 MiB Flash. No unique board identifier,
private backup hash, raw serial capture or user file is included here.

Before the public-layout reorganization, hardware sessions demonstrated:

* Flash loader -> Linux 6.12 -> independent cramfs root -> BusyBox hush.
* uname, proc/sysfs and external file tools, with fresh PID1 challenge responses.
* SRAM Flash handoff, exact JEDEC ID and a real userspace MTD sample matching backup.
* JFFS2 /data mounted rw, a freshly written/synced file, and identical contents
  after the operator's power-cycle procedure. The final transmitted input only
  read mounts/file and exited; it did not recreate the file. Observed USB absence
  exceeded 16 seconds. Power removal itself was not independently instrumented.
* Clean final diagnostic ledgers: errors/exit/clock_faults zero, matching
  IRQ/callback/ACK/jiffies counts, and restoration of the port's own permissions.

This is one bounded synced-file persistence result, not long-term reliability,
complete memory isolation, wear/GC qualification or general reset support.
The full non-data Flash comparison after runtime writes remains pending.

Host suites before reorganization: 2 SRAM, 2 rootfs, 14 kernel (1 skipped),
17 loader and 27 Flash-tool checks. Local sources/toolchain were reused.
After moving paths, BusyBox, cramfs, kernel, DTB, SRAM service and loaders were
rebuilt successfully using the local upstream references and existing toolchain.
The same 62 host checks passed with one kernel check skipped, plus 3 publication
tests for leak rejection, source changes and anonymous archive metadata.
The kernel patch/source hashes remain identical to the earlier port.
This is not a clean-network/toolchain rebuild; old hardware PASS is not
automatically attached to the newly built binaries.

The published source carries no raw private evidence archives. Maintainers can
provide a separately reviewed, redacted excerpt if needed. Follow BOARD.md to
produce your own device-specific results, keeping backups private.
