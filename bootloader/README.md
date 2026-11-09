# C5 Flash loader

Build with make flash-image at the project root. main.c reuses psram/main.c
through a generated Flash-only include, with bounded rootfs transfer adaptation.
See ../docs/BOARD.md for backup, explicit writes, readback and capture.
No historical board backup paths are hard-coded into public instructions.
