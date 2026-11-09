# Licensing and reference notices

This repository assembles platform code, build tooling and Linux patches.
There is no implied blanket relicensing of upstream work.

* Linux patches retain the Linux kernel's GPL-2.0-only and applicable file notices.
  The patch contains upstream context; see LICENSES/GPL-2.0.txt and Linux COPYING.
* New files with explicit SPDX identifiers retain those identifiers.
* Downloaded Linux, BusyBox, Buildroot, toolchain and ESP-IDF sources are not
  redistributed by the source exporter. Their own licenses apply when fetched.
* ESP-IDF v5.5.1 (Espressif) is the pinned reference for C5 register definitions,
  ROM ABI/addresses, PSRAM and cache behavior. Its upstream licenses/notices apply
  to upstream material; calling a ROM ABI does not make this an ESP-IDF application.
* Local STM32H750 reference: the project described at
  https://hackmd.io/@rota1001/stm32h750-linux (build/layout reference).
* S3 references: https://github.com/jcmvbkbc/esp32-linux-build and
  https://github.com/paulneja/Linux-on-esp32-S3 (architecture/build/test references).
  This repository does not bundle those projects or their binaries.

The license for independent files without an existing SPDX notice is awaiting
maintainer selection. No additional reuse permission is invented for them by
this document. Resolve that selection before presenting a unified project license.
