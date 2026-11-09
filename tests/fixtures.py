"""Synthetic console fixtures; no private board captures or identifiers."""
LEDGER=b'C5 MEM v1 result=00000000 ram_start=42000000 ram_end=42800000 kernel_end=42100000 dt_start=427f0000 kernel_reserved=00000001 dt_nomap=00000001 total_pages=00000600 free_pages=000005f0 page_addr=427ee000 page_checks=00000400 slab_checks=00000040 irq_off=00000001\r\n'
NONCE=b'0123456789abcdef'
HEAD=b'C5 USER ENTER entry=421000de\r\nBusyBox v1.37.0 hush - the humble shell\r\n/ # '
MATH=b'C5S:'+NONCE+b':42:1\r\n/ # '
LOOP=b'\r\nabc\r\n/ # '
END=b'C5END:'+NONCE+b'\r\n/ # '
SHELL_LEDGER=b'C5 SHELL v1 result=00000000 pid=00000001 entered=00000001 calls=00000029 returns=00000028 seen=00000059 errors=00000000 exit_code=00000000 enosys=00000000 rx_bytes=00000096 tx_bytes=00000200 polls=00000050 protection_restored=00000001 irq_count=00000050 callbacks=00000050 acked=00000050 jiffies_delta=00000050 clock_faults=00000000 source_off=00000001 irq_off=00000001 level_after=00000000\r\n'
SHELL_OUTPUT=HEAD+MATH+LOOP+END+SHELL_LEDGER
