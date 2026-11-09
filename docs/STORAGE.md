# Storage and boot architecture

ROM -> internal SRAM loader -> initialize PSRAM -> read/check kernel, DTB and
cramfs from external Flash -> jump to Linux -> mount cramfs -> /init -> hush.

| Flash offset | Purpose |
|---|---|
| 0x2000 | ESP ROM-format SRAM loader |
| 0x10000 | v2 length/FNV metadata |
| 0x20000 | kernel, maximum 2 MiB |
| 0x220000 | DTB |
| 0x230000 | independent cramfs, maximum 960 KiB |
| 0x400000..0x800000 | dedicated 4 MiB JFFS2 data region |

The boot packager checks sizes/overlaps; plan.json records exact erase bounds.
The five-payload writer excludes data. Full-chip restore does not preserve it.

Rootfs is staged at PSRAM 0x42700000..0x427f0000 and exposed as a read-only MTD
ROM mapping. /etc and /home remain in that readonly root. /tmp and /run are
volatile ramfs; /data is actual Flash-backed JFFS2 only in write-enabled mode.
This rootfs allocation remains until direct Flash root reads are implemented.

The runtime service occupies internal SRAM 0x40808000..0x4080e000. Its entry
switches stack before cache operations. Request/bounce storage is in SRAM.
Linux serializes access, disables IRQs for a short transaction, and restores
them for chip-busy polling. The service saves/restores branch prediction and
SPI1 configuration around cache writeback/suspend/resume. It uses CS0 only;
SPI0's PSRAM mapping is unchanged.

Both service and driver restrict operations to the data window. Exact JEDEC ID
0x174020 is required. Program requests are at most 64 bytes without crossing a
256-byte page; erase is a single aligned 4 KiB sector. Every program/erase is
read-verified. There is no chip erase, status unlock or eFuse command. A stuck
controller fail-stops in SRAM; other write/erase errors disable subsequent IO.
The command token is an internal policy tag, not a credential or isolation boundary.

Direct Flash rootfs, arbitrary erase/GC stress, space exhaustion and abrupt
power loss during writes remain future work. The successful synced-file test
must not be generalized to those scenarios.

Primary implementation references are the pinned ESP-IDF v5.5.1 C5 ROM/register
headers, Linux 6.12 MTD/JFFS2 code, and the XMC XM25QH64C datasheet. Preserve
upstream notices; see NOTICE.md.
