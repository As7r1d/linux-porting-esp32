#!/usr/bin/env python3
"""Offline transfer-boundary, integrity and result checks; no board operations."""
from pathlib import Path
import re,runpy,struct,subprocess,tempfile,unittest,contextlib,io,types
import os, pty, termios
from unittest.mock import patch
ROOT=Path(__file__).resolve().parent
R=runpy.run_path(str(ROOT/'run.py'))
S=(ROOT/'main.c').read_text()
LEDGER=b'C5 MEM v1 result=00000000 ram_start=42000000 ram_end=42800000 kernel_end=42100000 dt_start=427f0000 kernel_reserved=00000001 dt_nomap=00000001 total_pages=00000600 free_pages=000005f0 page_addr=427ee000 page_checks=00000400 slab_checks=00000040 irq_off=00000001\r\n'
NONCE=b'0123456789abcdef'
ASSESS=R['assess'];R['assess']=lambda raw:ASSESS(raw,NONCE)
PROTOCOL=R['ShellProtocol'](NONCE)
HEAD=b'C5 USER ENTER entry=421000de\r\nBusyBox v1.37.0 hush - the humble shell\r\n/ # '
MATH=b'C5S:'+NONCE+b':42:1\r\n/ # '
LOOP=b'\r\nabc\r\n/ # '
END=b'C5END:'+NONCE+b'\r\n/ # '
SHELL_LEDGER=b'C5 SHELL v1 result=00000000 pid=00000001 entered=00000001 calls=00000029 returns=00000028 seen=00000059 errors=00000000 exit_code=00000000 enosys=00000000 rx_bytes=00000096 tx_bytes=00000200 polls=00000050 protection_restored=00000001 irq_count=00000050 callbacks=00000050 acked=00000050 jiffies_delta=00000050 clock_faults=00000000 source_off=00000001 irq_off=00000001 level_after=00000000\r\n'
SHELL_OUTPUT=HEAD+MATH+LOOP+END+SHELL_LEDGER


class Checks(unittest.TestCase):
    def test_actual_c_header_packet_and_address_validation(self):
        pure=S[S.index('static uint32_t fnv('):S.index('static void receive(')]
        pure+=S[S.index('static uint32_t valid_header('):S.index('static void transfer(')]
        harness='''#include <stdint.h>
#define MAX_IMAGE 0x200000u
#define DT_OFF 0x7f0000u
static uint32_t header[16];
'''+pure+r'''
int main(void) {
 uint32_t p[19]={0};
 header[0]=0x354c444b;header[1]=1;header[2]=0x100000;header[3]=64;
 if(!valid_header(header))return 1;
 header[2]=0x200040;if(valid_header(header))return 2;
 header[2]=0x100001;if(valid_header(header))return 3;
 header[2]=0x100000;header[6]=1;if(valid_header(header))return 4;
 header[6]=0;
 if(!allowed_offset(0)||!allowed_offset(0xfffc0)||!allowed_offset(DT_OFF))return 5;
 if(allowed_offset(1)||allowed_offset(0x100000)||allowed_offset(DT_OFF+64)||allowed_offset(0xffffffc0))return 6;
 p[0]=3;p[2]=64;p[1]=fnv(2166136261,(uint8_t *)(p+3),64);
 if(!valid_packet(p,3)||valid_packet(p,2))return 7;
 p[3]=1;if(valid_packet(p,3))return 8;
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'t.c').write_text(harness)
            subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror',str(p/'t.c'),'-o',str(p/'t')],check=True)
            subprocess.run([str(p/'t')],check=True)

    def test_parser_requires_real_ordered_banner_and_checkpoint(self):
        good=b'C5 LOAD JUMP\r\nLinux version 6.12 test\r\n'+LEDGER+SHELL_OUTPUT+R['CHECKPOINT']+b'\r\n'
        self.assertEqual(R['assess'](good)[0],0)
        # Upload ACKs add many CRLFs before the kernel output. All indices
        # must use the same normalized buffer.
        self.assertEqual(R['assess'](b'C5 LOAD ACK 00000000\r\n'*15469+good)[0],0)
        for raw in [good.replace(SHELL_OUTPUT,b''),good.replace(LEDGER,b''),good[:-2],good.replace(b'Linux version',b'Loader version'),good.replace(b'C5 LOAD JUMP',b''),R['CHECKPOINT']+b'\nLinux version 6.12\nC5 LOAD JUMP\n']:
            self.assertEqual(R['assess'](raw)[0],2)
        for extra in [b'\n!',b'C5 LOAD FATAL code=1',b'C5 LOAD TRAP cause=1',b'C5 MEM FAIL',b'Kernel panic']:
            self.assertEqual(R['assess'](good+extra)[0],1)

    def test_memory_ledger_rejects_corrupted_fields(self):
        values=dict(zip(R['MEM_NAMES'],(int(x,16) for x in R['MEM_ROW'].search(LEDGER).groups())))
        for name,value in values.items():
            bad_value=(value^1) if name not in ('kernel_end','total_pages','free_pages') else 0
            corrupted=LEDGER.replace(f'{name}={value:08x}'.encode(),f'{name}={bad_value:08x}'.encode())
            raw=b'C5 LOAD JUMP\nLinux version 6.12\n'+corrupted+SHELL_OUTPUT+R['CHECKPOINT']+b'\n'
            with self.subTest(name=name):self.assertEqual(R['assess'](raw)[0],1)

    def test_shell_requires_calculated_results_and_nonce(self):
        good=b'C5 LOAD JUMP\nLinux version 6.12\n'+LEDGER+SHELL_OUTPUT+R['CHECKPOINT']+b'\n'
        values=dict(zip(R['SHELL_NAMES'],(int(v,16) for v in R['SHELL_ROW'].search(SHELL_LEDGER).groups())))
        for name,value in values.items():
            bad=value^1
            if name in ('calls','returns','rx_bytes','tx_bytes','polls','irq_count'):bad=0
            if name=='enosys':bad=512
            corrupted=SHELL_OUTPUT.replace(f'{name}={value:08x}'.encode(),f'{name}={bad:08x}'.encode())
            raw=b'C5 LOAD JUMP\nLinux version 6.12\n'+LEDGER+corrupted+R['CHECKPOINT']+b'\n'
            with self.subTest(name=name):self.assertEqual(R['assess'](raw)[0],1)
        for block in [HEAD,MATH,LOOP,END]:
            self.assertEqual(R['assess'](good.replace(block,b''))[0],2)
        self.assertEqual(ASSESS(good,b'fedcba9876543210')[0],2)
        self.assertEqual(R['assess'](good.replace(b':42:1',b':41:1'))[0],2)
        echo_only=good.replace(MATH,b'').replace(END,b'')+b''.join(PROTOCOL.commands)
        self.assertEqual(R['assess'](echo_only)[0],2)

    def test_prompt_protocol_waits_for_each_command_result(self):
        p=R['ShellProtocol'](NONCE)
        self.assertIsNone(p.next(b'Linux version 6.12 / # '))
        raw=HEAD
        self.assertEqual(p.next(raw),p.commands[0]);self.assertIsNone(p.next(raw))
        raw+=p.commands[0]+b'/ # '
        self.assertEqual(p.next(raw),p.commands[1]);self.assertIsNone(p.next(raw))
        raw+=p.commands[1]+MATH
        self.assertEqual(p.next(raw),p.commands[2])
        raw+=p.commands[2]+LOOP
        self.assertEqual(p.next(raw),p.commands[3])
        raw+=p.commands[3]+END
        self.assertEqual(p.next(raw),b'exit 0\n');self.assertIsNone(p.next(raw))

    def test_payload_headers_and_limits(self):
        image=bytearray(64);image[56:60]=b'RSC\x05';struct.pack_into('<Q',image,16,0x1000)
        dt=bytearray(40);dt[:4]=b'\xd0\x0d\xfe\xed';struct.pack_into('>I',dt,4,40)
        a,b=R['validate_payloads'](image,dt);self.assertEqual((len(a),len(b)),(64,64))
        for off,value in [(8,1),(16,0x7f0000)]:
            bad=bytearray(image);struct.pack_into('<Q',bad,off,value)
            with self.assertRaises(ValueError):R['validate_payloads'](bad,dt)
        with self.assertRaises(ValueError):R['validate_payloads'](image,dt[:-1])
        self.assertEqual(R['fnv'](b'hello'),0x4f9f2cab)

    def test_long_session_tick_and_syscall_limits(self):
        row=SHELL_LEDGER.replace(b'calls=00000029',b'calls=00000258').replace(b'returns=00000028',b'returns=00000257')
        for key in ['irq_count','callbacks','acked','jiffies_delta','polls']:
            row=re.sub(key.encode()+rb'=[0-9a-f]{8}',key.encode()+b'=00002ee0',row)
        data=b'C5 LOAD JUMP\nLinux version 6.12\n'+LEDGER+HEAD+MATH+LOOP+END+row+R['CHECKPOINT']+b'\n'
        self.assertEqual(ASSESS(data,NONCE)[0],0)
        self.assertEqual(ASSESS(data.replace(b'irq_count=00002ee0',b'irq_count=00008000'),NONCE)[0],1)
        self.assertEqual(ASSESS(data.replace(b'calls=00000258',b'calls=00001001'),NONCE)[0],1)

    def test_repeat_rejects_missing_children_and_page_drift(self):
        data=b''.join(f'C5 USER EXEC pid={15+n} entry=0\n'.encode() for n in range(6))+(b'C5FILE:external-cat\n\nc5-proof\n'*3)
        data+=b''.join(b'C5ROUND:'+NONCE+b':'+str(n).encode()+b'\n' for n in range(1,4))
        data+=b'C5STRESS:'+NONCE+b':returned\n'
        samples=b''.join(f'C5 REAP n={n} pid={14+n} free=1500\n'.encode() for n in range(1,7))
        self.assertEqual(R['assess_repeat'](data+samples,NONCE)[0],0)
        self.assertEqual(R['assess_repeat'](data+samples,NONCE[::-1])[0],2)
        self.assertEqual(R['assess_repeat'](data+samples.replace(b'n=6',b'n=7'),NONCE)[0],2)
        self.assertEqual(R['assess_repeat'](data+samples.replace(b'n=6 pid=20 free=1500',b'n=6 pid=20 free=1495'),NONCE)[0],1)

    def test_repeat_waits_for_last_round_and_continuation(self):
        p=R['ShellProtocol'](NONCE,probe_repeat=True)
        raw=HEAD
        self.assertEqual(p.next(raw),p.commands[0])
        for reply,stage in [(b'/ # ',1),(MATH,2),(LOOP,3),(END,4)]:
            raw+=reply
            self.assertEqual(p.next(raw),p.commands[stage])
        self.assertIsNone(p.next(raw+b'/ # '))
        raw+=b'C5ROUND:'+NONCE+b':3\n/ # '
        self.assertEqual(p.next(raw),p.commands[5])
        self.assertIsNone(p.next(raw+b'/ # '))
        raw+=b'C5STRESS:'+NONCE+b':returned\n/ # '
        self.assertEqual(p.next(raw),b'exit 0\n')

    def test_external_exec_waits_for_each_output(self):
        p=R['ShellProtocol'](NONCE,probe_exec=True)
        raw=HEAD
        self.assertEqual(p.next(raw),p.commands[0])
        for reply,stage in [(b'/ # ',1),(MATH,2),(LOOP,3),(END,4)]:
            raw+=reply
            self.assertEqual(p.next(raw),p.commands[stage])
        self.assertEqual(p.commands[4],b'/bin/cat /etc/c5-proof\n')
        self.assertIsNone(p.next(raw+b'/ # '))
        raw+=b'C5FILE:external-cat\n/ # '
        self.assertEqual(p.next(raw),b'/bin/ls /etc\n')
        self.assertIsNone(p.next(raw+b'/ # '))
        raw+=b'\nc5-proof\n/ # '
        self.assertEqual(p.next(raw),p.commands[6])
        self.assertIsNone(p.next(raw+b'/ # '))
        raw+=b'C5EXEC:'+NONCE+b':returned\n/ # '
        self.assertEqual(p.next(raw),b'exit 0\n')

    def test_missing_command_probe_requires_return_prompt(self):
        p=R['ShellProtocol'](NONCE,True)
        raw=HEAD
        self.assertEqual(p.next(raw),p.commands[0])
        for reply,stage in [(b'/ # ',1),(MATH,2),(LOOP,3),(END,4)]:
            raw+=reply
            self.assertEqual(p.next(raw),p.commands[stage])
        self.assertEqual(p.commands[4],b'c5_missing_command_probe\n')
        self.assertIsNone(p.next(raw))
        raw+=b"hush: can't execute 'c5_missing_command_probe': No such file or directory\n/ # "
        self.assertEqual(p.next(raw),p.commands[5])
        self.assertIsNone(p.next(raw))
        raw+=b'C5PROC:'+NONCE+b':returned\n/ # '
        self.assertEqual(p.next(raw),b'exit 0\n')
        self.assertIsNone(p.next(raw))

    def test_rom_reset_after_jump_is_failure(self):
        for marker in [b'CPU_LOCKUP',b'ESP-ROM:esp32c5',b'waiting for download']:
            self.assertEqual(ASSESS(b'C5 LOAD JUMP\n'+HEAD+marker,NONCE)[0],1)
        self.assertEqual(ASSESS(b'ESP-ROM:esp32c5\n',NONCE)[0],2)

    def test_terminal_input_and_restoration(self):
        master,slave=pty.openpty()
        try:
            saved=termios.tcgetattr(slave)
            terminal=R['InteractiveInput'](types.SimpleNamespace(fileno=lambda:slave))
            try:
                os.write(master,b'echo hello\n')
                self.assertEqual(terminal.read(),b'echo hello\n')
                self.assertFalse(termios.tcgetattr(slave)[3] & termios.ECHO)
            finally:
                terminal.close()
            self.assertEqual(termios.tcgetattr(slave),saved)
        finally:
            os.close(master);os.close(slave)

    def test_manual_escape_requests_exit_and_restores_terminal(self):
        self.transfer_mock(True)

    def test_host_transfer_handshake_with_mock_device(self):
        self.transfer_mock(False)

    def transfer_mock(self, interactive):
        # Exercises execute() framing/order without touching serial or claiming HW success.
        class Port:
            def __init__(self, **kw): self.buf=b''; self.seq=0; self.closed=False;self.commands=0
            def open(self): pass
            def close(self): self.closed=True
            @property
            def in_waiting(self): return len(self.buf)
            def read_until(self, token, size):
                n=self.buf.find(b'\n')+1
                out,self.buf=self.buf[:n],self.buf[n:]
                return out
            def read(self,n):
                out,self.buf=self.buf[:n],self.buf[n:];return out
            def write(self,data):
                if len(data)==64:
                    h=struct.unpack('<16I',data)
                    assert h[:4]==(0x354c444b,1,64,64)
                    self.buf+=b'C5 LOAD BEGIN\r\n'
                elif len(data)==76:
                    seq,checksum,length=struct.unpack('<3I',data[:12])
                    assert seq==self.seq and length==64 and checksum==R['fnv'](data[12:])
                    self.buf+=f'C5 LOAD ACK {seq:08x}\r\n'.encode();self.seq+=1
                    if self.seq==2:self.buf+=b'C5 LOAD READY-JUMP entry=42000000 dt=427f0000\r\n'
                elif data==b'GO!!':
                    assert self.seq==2
                    self.buf+=b'C5 LOAD JUMP\r\nLinux version 6.12 mock-only\r\n'+LEDGER+HEAD
                else:
                    assert data==(b"\x15exit 0\n" if interactive and self.commands==4 else PROTOCOL.commands[self.commands])
                    replies=[b'/ # ',MATH,LOOP,END,SHELL_LEDGER+R['CHECKPOINT']+b'\r\n']
                    self.buf+=data+replies[self.commands];self.commands+=1
                return len(data)
        port=Port()
        class ROM:
            def __init__(self,p): assert p is port
            def connect(self,**kw): pass
            def flash_spi_attach(self,n): assert n==0
            def flash_id(self,**kw): return 0x174020
        def load(esp,data): port.buf+=b'C5 LOAD READY v1\r\n'
        class Terminal:
            closed=False
            def __init__(self, stream): pass
            def read(self): return b'\x1d'
            def close(self): Terminal.closed=True
        g=R['execute'].__globals__
        with tempfile.TemporaryDirectory() as d, patch.dict(g,InteractiveInput=Terminal,serial=types.SimpleNamespace(Serial=lambda **kw:port),ESP32C5ROM=ROM,load_ram=load,secrets=types.SimpleNamespace(token_hex=lambda n:NONCE.decode())), contextlib.redirect_stdout(io.StringIO()):
            code=R['execute'](types.SimpleNamespace(port='mock',interactive=interactive),b'loader',bytes(64),bytes(64),Path(d)/'raw.bin')
        self.assertEqual(code,0);self.assertEqual(port.seq,2);self.assertTrue(port.closed);self.assertEqual(port.commands,5)
        self.assertEqual(Terminal.closed,interactive)

    def test_actual_ack_is_one_bounded_usb_packet(self):
        func=S[S.index('static void send_ack('):S.index('static void load_segment(')]
        harness=r'''#include <stdint.h>
#include <stdlib.h>
#include <string.h>
static uint32_t fifo[64], count, conf=2;
static uint32_t *capture(void) { if(count>=64)exit(2); return &fifo[count++]; }
#define USB_EP1 (*capture())
#define USB_EP1_CONF conf
#define USB_TX_FREE 2u
#define USB_WR_DONE 1u
static void fatal(uint32_t code) { (void)code;exit(3); }
'''+func+r'''
int main(void) {
 send_ack(0x1234abcd);if(count!=22||conf!=1)return 1;
 const char *want="C5 LOAD ACK 1234abcd\r\n";
 for(unsigned i=0;i<22;i++)if(fifo[i]!=(unsigned char)want[i])return 2;
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'t.c').write_text(harness)
            subprocess.run(['cc','-O2','-Wall','-Wextra','-Werror',str(p/'t.c'),'-o',str(p/'t')],check=True)
            subprocess.run([str(p/'t')],check=True)

    def test_spi_cs1_only_and_no_flash_write(self):
        transfer=S[S.index('static void transfer('):S.index('static void load_segment(')]
        self.assertIn('rom_spi_start(1,write?0:(uint8_t *)words,write?0:64,2,0)',transfer)
        self.assertIn('SPI1_MISC=(SPI1_MISC|1u)&~2u',transfer)
        self.assertNotIn('write_flash',(ROOT/'run.py').read_text())
        self.assertIn('for (uint32_t page=0;page<128;++page)',S)
        self.assertIn('fnv(2166136261u,(const uint8_t *)BASE,header[2])',S)
        self.assertIn('if(go!=0x21214f47u)fatal(22)',S)

if __name__=='__main__':unittest.main()
