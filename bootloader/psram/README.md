# Shared PSRAM bootstrap

main.c contains the C5 PSRAM initialization and transfer primitives reused by
the Flash loader through a generated include. The internal SRAM linker/ROM
configuration and host format checks are retained. The old Python RAM uploader
and its protocol experiments are not shipped. Use the Flash workflow documented
in ../../docs/BOARD.md; terminal support is in ../console.py.
