# Kernel platform sources

platform/ contains the canonical new C5 kernel C/assembly/header files, laid
out using Linux paths. linux.patch at the root only changes existing upstream
files: Kconfig/Kbuild, entry, signal handling and other integration hooks.
The Flash request ABI is canonical in flash-service/abi.h and installed once.

prepare.sh verifies the pristine inputs, copies Linux, applies the integration
patch and runs install.sh. pristine.sha256 and port.sha256 record the expected
upstream inputs and complete ported outputs. Existing unexpected working-tree
changes fail checks rather than being silently overwritten. Build from the root
with make kernel; checks are under tests/ and run with make check.
