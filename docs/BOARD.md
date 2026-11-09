# Board operations

Use the board's native USB Serial/JTAG connection and a data-capable cable.
Close other serial clients. Determine the actual device with
`ls -l /dev/serial/by-id/`; do not assume an ACM number. The following PORT and
BACKUP are placeholders, not paths from a developer's machine.

Activate the Python environment. To enter ROM download mode, hold BOOT,
press/release RESET/EN, then release BOOT (or power on while holding BOOT).
The current Linux USB policy may prevent automatic entry.

```sh
bash bootloader/preflight.sh PORT
```

This records live security/identity information and reads the complete 8 MiB
Flash. It does not write Flash/eFuses. Require READ-ONLY COMPLETE, retain the
private backup, and use the printed directory as BACKUP. Never upload that
directory, raw captures or device security/identity records to an issue.

```sh
python -B bootloader/flash.py --preflight BACKUP
python -B -u bootloader/flash.py --preflight BACKUP --port PORT --write
```

The first command is offline only. The second overwrites the reviewed boot/system
regions, checks live identity/content, and verifies every payload by readback.
Do not use an old backup review for a different erase layout. The optional
--previous-write accepts only a matching readback-verified installation baseline.
It is not automatic rollback. After READBACK VERIFIED:

```sh
python -B -u bootloader/capture.py --port PORT --power-cycle --init-test
```

At WAIT remove every supply for at least five seconds and reconnect USB, without
BOOT/RESET. For manual operation use --interactive instead of --init-test.
Only complete ledgers/checkpoint qualify a session. exit 0 intentionally halts.
USB disappearance alone does not independently establish all-power removal.

Runtime data writes require a STORAGE_WRITE=1 build. Such a boot mounts JFFS2
and may initialize/recover the dedicated data region. Keep a full private backup
before trying it. A persistence test writes a fresh challenge under /data,
runs sync, exits, removes all power, boots again and reads without rewriting.
Record the expected challenge privately. Do not publish raw Flash contents.

Full recovery is an explicit whole-chip overwrite:
`python -B -u bootloader/flash.py --preflight BACKUP --port PORT --restore`.
It requires the original owner backup and ROM download mode, replaces data too,
and is not a substitute for a separately tested recovery procedure.
