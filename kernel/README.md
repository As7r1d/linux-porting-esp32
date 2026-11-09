# Kernel

Root linux.config/linux.patch and kernel/source-hashes.json define the port.
prepare.py creates a private patched source copy. Build with make kernel at the
project root; flash-service/ is a separately linked SRAM runtime helper.
