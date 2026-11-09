#!/usr/bin/env python3
"""Terminal input and bounded diagnostic ledger validation; no uploader code."""
import os,re,select,termios,tty
CHECKPOINT=b'C5 SHELL CHECKPOINT: BusyBox hush PID1 returned; intentional halt at diagnostic exit'

class InteractiveInput:
    """Local cbreak input; the remote canonical tty handles editing and echo."""
    def __init__(self, stream):
        self.fd = stream.fileno()
        self.saved = termios.tcgetattr(self.fd)
        try:
            tty.setcbreak(self.fd)
        except BaseException:
            self.close()
            raise

    def read(self):
        if not select.select([self.fd], [], [], 0)[0]:
            return None
        return os.read(self.fd, 256)

    def close(self):
        if self.saved is not None:
            termios.tcsetattr(self.fd, termios.TCSANOW, self.saved)
            self.saved = None


MEM_NAMES='result ram_start ram_end kernel_end dt_start kernel_reserved dt_nomap total_pages free_pages page_addr page_checks slab_checks irq_off'.split()
MEM_ROW=re.compile(rb'C5 MEM v1 '+b' '.join(name.encode()+rb'=([0-9a-f]{8})' for name in MEM_NAMES)+rb'\r?\n')

SHELL_NAMES='result pid entered calls returns seen errors exit_code enosys rx_bytes tx_bytes polls protection_restored irq_count callbacks acked jiffies_delta clock_faults source_off irq_off level_after'.split()
SHELL_ROW=re.compile(rb'C5 SHELL v1 '+b' '.join(name.encode()+rb'=([0-9a-f]{8})' for name in SHELL_NAMES)+rb'\r?\n')

def assess(raw, nonce):
    data=raw.replace(b'\r\n',b'\n')
    if any(token in raw for token in [b'C5 LOAD FATAL',b'C5 LOAD TRAP',b'C5 MEM FAIL',b'C5 USER FAIL',b'C5 SHELL FAIL',b'Kernel panic',b'Oops:',b'BUG:',b'Warning: unable to open an initial console.']) or re.search(rb'(?:^|[\r\n])!',raw):
        return 1,'FAIL: loader/kernel/BusyBox shell checkpoint failure; no recovery claim'
    if b'C5 LOAD JUMP' in data and any(token in data[data.index(b'C5 LOAD JUMP'):] for token in (b'CPU_LOCKUP', b'ESP-ROM:esp32c5', b'waiting for download')):
        return 1,'FAIL: board reset during Linux shell session; no successful diagnostic exit/restoration claim'
    memory=list(MEM_ROW.finditer(data))
    for match in memory:
        row=dict(zip(MEM_NAMES,(int(v,16) for v in match.groups())))
        expected=dict(result=0,ram_start=0x42000000,ram_end=0x42800000,dt_start=0x427f0000,kernel_reserved=1,dt_nomap=1,page_checks=1024,slab_checks=64,irq_off=1)
        if (any(row[k]!=v for k,v in expected.items()) or not 0x42000000<row['kernel_end']<0x427f0000 or not 0<row['free_pages']<=row['total_pages']<=2032 or row['page_addr']%4096 or not row['kernel_end']<=row['page_addr']<=0x427ef000):
            return 1,'FAIL: memory ledger/reservations'
    shell=list(SHELL_ROW.finditer(data))
    for match in shell:
        row=dict(zip(SHELL_NAMES,(int(v,16) for v in match.groups())))
        expected=dict(result=0,pid=1,entered=1,errors=0,exit_code=0,protection_restored=1,clock_faults=0,source_off=1,irq_off=1,level_after=0)
        if (any(row[k]!=v for k,v in expected.items()) or row['seen']&0x49!=0x49 or not 10<=row['calls']<=4096 or row['returns']+1!=row['calls'] or row['enosys']>row['returns'] or not 1<=row['rx_bytes']<=65536 or not 1<=row['tx_bytes']<=262144 or not 1<=row['polls']<32768 or not 2<=row['irq_count']<32768 or any(row[k]!=row['irq_count'] for k in ['callbacks','acked','jiffies_delta'])):
            return 1,'FAIL: shell syscall/context/tty/clock ledger'
    if not memory or not shell:return 2,'INCONCLUSIVE: complete memory/shell ledgers required'
    return 0,'PASS (limited): memory and shell ledgers valid'
