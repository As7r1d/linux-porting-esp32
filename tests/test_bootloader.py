#!/usr/bin/env python3
"""Offline ROM-image, erase-layout and actual Flash-copy routine tests."""
from pathlib import Path
import unittest,struct,subprocess,tempfile,json,hashlib,runpy,re
ROOT=Path(__file__).resolve().parent.parent/'bootloader'
class Checks(unittest.TestCase):
    def test_rom_image_and_payload_integrity(self):
        from esptool.targets.esp32c5 import ESP32C5ROM
        from esptool.bin_image import LoadFirmwareImage
        self.assertEqual(ESP32C5ROM.BOOTLOADER_FLASH_OFFSET,0x2000)
        image=LoadFirmwareImage('esp32c5',(ROOT/'build/bootloader.bin').read_bytes())
        image.verify();self.assertEqual(image.entrypoint,0x40800000)
        self.assertEqual(image.stored_digest,image.calc_digest)
        self.assertEqual(image.checksum,image.calculate_checksum())
        symbols=subprocess.check_output(['riscv64-unknown-elf-nm',str(ROOT/'build/bootloader.elf')],text=True)
        self.assertNotIn('usb_loader_unused',symbols)
        self.assertNotIn(' receive',symbols)
        for segment in image.segments:
            self.assertLessEqual(segment.addr+len(segment.data),0x40806000)
        h=struct.unpack('<16I',(ROOT/'build/metadata.bin').read_bytes())
        module=runpy.run_path(str(ROOT/'build.py'))
        self.assertEqual(h[:2],(0x354c444b,2));self.assertEqual(h[8:],(0,)*8)
        fs=(ROOT/'build/rootfs.cramfs').read_bytes()
        self.assertEqual((len(fs),module['fnv'](fs)),h[6:8])
        for n,name in enumerate(['kernel.bin','dtb.bin']):
            b=(ROOT/'build'/name).read_bytes()
            self.assertEqual(len(b),h[2+n]);self.assertEqual(module['fnv'](b),h[4+n])
        for line in (ROOT/'build/SHA256SUMS').read_text().splitlines():
            expected,name=line.split(maxsplit=1)
            self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),expected)

    def test_actual_copy_and_corruption_rejection(self):
        source=(ROOT/'main.c').read_text()
        func=source[source.index('static void from_flash('):source.index('__attribute__((noreturn)) void diagnostic_main')]
        original=(ROOT.parent/'bootloader/psram/main.c').read_text()
        fnv=original[original.index('static uint32_t fnv('):]
        # Keep only the pure FNV function.
        fnv=fnv[:fnv.index('\n}',fnv.index('{'))+2]
        code='''#include <stdint.h>
#include <stdlib.h>
#include <string.h>
static uint32_t packet[19],verify[16];
static uint8_t flash[128],ram[128];
static void fatal(int n){exit(n);}
static void flash_read64(uint32_t off,uint32_t *p){if(off>64)exit(1);memcpy(p,flash+off,64);}
static void transfer(uint32_t wr,uint32_t off,uint32_t *p){if(off>64)exit(2);if(wr)memcpy(ram+off,p,64);else memcpy(p,ram+off,64);}
'''+fnv+func+'''
int main(int argc,char **argv){(void)argv;for(unsigned i=0;i<128;i++)flash[i]=i;
 uint32_t hash=fnv(2166136261u,flash,128);from_flash(0,0,128,hash+(argc>1));
 return memcmp(ram,flash,128)!=0;}
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'t.c').write_text(code)
            subprocess.run(['cc','-Wall','-Wextra','-Werror',str(p/'t.c'),'-o',str(p/'t')],check=True)
            self.assertEqual(subprocess.run([str(p/'t')]).returncode,0)
            self.assertEqual(subprocess.run([str(p/'t'),'bad-hash']).returncode,33)

    def test_sector_layout_and_cs_read_only(self):
        plan=json.loads((ROOT/'build/plan.json').read_text())
        self.assertFalse(plan['flash_written']);self.assertFalse(plan['hardware_tested'])
        self.assertEqual([r['offset'] for r in plan['regions']],[0x2000,0x10000,0x20000,0x220000,0x230000])
        for a,b in zip(plan['regions'],plan['regions'][1:]):self.assertLessEqual(a['erase_end'],b['offset'])
        for r in plan['regions']:self.assertLessEqual(r['erase_end'],0x800000)
        source=(ROOT/'main.c').read_text()
        read=source[source.index('static void flash_read64'):source.index('static void from_flash')]
        self.assertIn('.command=3',read);self.assertIn('dest,64,1,0)',read)
        self.assertNotIn('.tx=',read)
        main=source[source.index('__attribute__((noreturn)) void diagnostic_main'):]
        self.assertNotIn('receive(',main)

    def test_v2_header_rejects_bad_rootfs_bounds(self):
        source=(ROOT/'main.c').read_text()
        loader=(ROOT.parent/'bootloader/psram/main.c').read_text()
        old=loader[loader.index('static uint32_t valid_header('):loader.index('static uint32_t valid_packet(')]
        new=source[source.index('static int flash_header_valid('):source.index('static void boot_watchdogs(')]
        code='#include <stdint.h>\n#define MAX_IMAGE 0x200000u\n#define ROOTFS_MAX 0xf0000u\n'+old+new+'''
int main(void) {
 uint32_t h[16]={0x354c444b,2,64,64,0,0,4096,0};
 if(!flash_header_valid(h)) return 1;
 h[1]=1; if(flash_header_valid(h)) return 2; h[1]=2;
 h[6]=0; if(flash_header_valid(h)) return 3;
 h[6]=0xf0040; if(flash_header_valid(h)) return 4;
 h[6]=65; if(flash_header_valid(h)) return 5;
 h[6]=4096; h[8]=1; if(flash_header_valid(h)) return 6;
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);(p/'t.c').write_text(code)
            subprocess.run(['cc','-Wall','-Wextra','-Werror',str(p/'t.c'),'-o',str(p/'t')],check=True)
            subprocess.run([str(p/'t')],check=True)

    def test_actual_psram_transfer_bounds_include_only_rootfs_window(self):
        source=(ROOT/'build/psram-loader.inc').read_text()
        start=source.index('static uint32_t allowed_offset(')
        function=source[start:source.index('static void transfer(',start)]
        code='''#include <stdint.h>
#define DT_OFF 0x7f0000u
static uint32_t header[16]={0x354c444b,2,128,64,0,0,128,0};
'''+function+'''
int main(void) {
 if(!allowed_offset(0) || !allowed_offset(64) || allowed_offset(128))return 1;
 if(!allowed_offset(DT_OFF) || allowed_offset(DT_OFF+64))return 2;
 if(!allowed_offset(0x700000) || !allowed_offset(0x700040))return 3;
 if(allowed_offset(0x700080) || allowed_offset(0x6fffc0))return 4;
 if(allowed_offset(0x700001) || allowed_offset(0xffffffc0))return 5;
 header[6]=0xf0000;
 if(!allowed_offset(0x7effc0) || allowed_offset(0x7f0040))return 6;
 header[6]=0; if(allowed_offset(0x700000))return 7;
 header[6]=0xf0040; if(allowed_offset(0x700000))return 8;
 header[6]=65; if(allowed_offset(0x700000))return 9;
 header[6]=128; header[1]=1; if(allowed_offset(0x700000))return 10;
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);(p/'t.c').write_text(code)
            subprocess.run(['cc','-Wall','-Wextra','-Werror',str(p/'t.c'),'-o',str(p/'t')],check=True)
            subprocess.run([str(p/'t')],check=True)

    def test_rootfs_copy_uses_actual_transfer_guard(self):
        main=(ROOT/'main.c').read_text()
        source=(ROOT/'build/psram-loader.inc').read_text()
        copy=main[main.index('static void from_flash('):main.index('__attribute__((noreturn)) void diagnostic_main')]
        start=source.index('static uint32_t allowed_offset(')
        bounds=source[start:source.index('static void transfer(',start)]
        start=source.index('static uint32_t fnv(')
        end=source.index('\n}',start)+2
        code='''#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#define DT_OFF 0x7f0000u
static uint32_t header[16]={0x354c444b,2,64,64,0,0,128,0};
static uint32_t packet[19],verify[16];
static uint8_t flash[128],ram[128];
static void fatal(int n){exit(n);}
'''+source[start:end]+bounds+'''
static void flash_read64(uint32_t off,uint32_t *p){
 if(off<0x230000 || off>0x230040)exit(90);
 memcpy(p,flash+off-0x230000,64);
}
static void transfer(uint32_t wr,uint32_t off,uint32_t *p){
 if(!allowed_offset(off) || wr>1)fatal(2);
 if(off<0x700000 || off>0x700040)exit(91);
 if(wr)memcpy(ram+off-0x700000,p,64);else memcpy(p,ram+off-0x700000,64);
}
'''+copy+'''
int main(int argc,char **argv){
 (void)argv;for(unsigned i=0;i<128;i++)flash[i]=(uint8_t)i;
 if(argc>1)header[1]=1;
 from_flash(0x230000,0x700000,128,fnv(2166136261u,flash,128));
 return memcmp(ram,flash,128)!=0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);(p/'t.c').write_text(code)
            subprocess.run(['cc','-Wall','-Wextra','-Werror',str(p/'t.c'),'-o',str(p/'t')],check=True)
            self.assertEqual(subprocess.run([str(p/'t')]).returncode,0)
            self.assertEqual(subprocess.run([str(p/'t'),'legacy']).returncode,2)

    def exercise_writer(self, fault=None):
        writer=runpy.run_path(str(ROOT/'flash.py'))
        plan=json.loads((ROOT/'build/plan.json').read_text())
        backup=bytes([255])*0x800000;flash=bytearray(backup);writes=[]
        identity='02:00:00:00:00:01'
        security='Flags: 0x00000000 \nSecure Boot: Disabled\nFlash Encryption: Disabled\nChip ID: 23\nBASE MAC: '+identity+'\n'
        if fault=='identity':security=security.replace(identity,'00:00:00:00:00:00')
        if fault=='before':flash[0x2000]=0
        if fault=='unchanged':
            for r in plan['regions']:
                data=(ROOT/'build'/r['file']).read_bytes()
                flash[r['offset']:r['offset']+len(data)]=data
            backup=bytes(flash)
        def run(command,rom=False):
            if command[0]=='get-security-info':
                self.assertTrue(rom);return security
            if command[0]=='read-flash':
                off,size=int(command[3],0),int(command[4],0)
                result=bytes(flash[off:off+size])
                if fault=='after' and writes:result=bytes([result[0]^1])+result[1:]
                Path(command[5]).write_bytes(result)
            elif command[0]=='write-flash':
                self.assertNotIn('--force',command)
                off=int(command[-2],0);path=Path(command[-1]);data=path.read_bytes()
                writes.append(path.name)
                end=(off+len(data)+4095)&~4095
                flash[off:end]=bytes([255])*(end-off);flash[off:off+len(data)]=data
            else:self.fail('Unexpected board operation: '+repr(command))
            return ''
        with tempfile.TemporaryDirectory() as d:
            if fault and fault!='unchanged':
                with self.assertRaises(ValueError):writer['program'](backup,identity,plan,Path(d),run)
            else:writer['program'](backup,identity,plan,Path(d),run)
        return writes

    def test_installed_baseline_requires_intact_readbacks(self):
        writer=runpy.run_path(str(ROOT/'flash.py'))
        plan=json.loads((ROOT/'build/plan.json').read_text())
        original=bytes([255])*0x800000
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'plan.json').write_text(json.dumps(plan))
            (p/'result.json').write_text(json.dumps(dict(action='write',readback_verified=True)))
            for n,r in enumerate(plan['regions'][1:]+plan['regions'][:1]):
                data=(ROOT/'build'/r['file']).read_bytes()
                (p/r['file']).write_bytes(data);(p/f'after-{n}.bin').write_bytes(data)
            baseline=writer['installed_baseline'](original,p,plan['regions'])
            for r in plan['regions']:
                self.assertEqual(baseline[r['offset']:r['offset']+r['bytes']],(p/r['file']).read_bytes())
            self.assertEqual(original,bytes([255])*0x800000)
            (p/'after-0.bin').write_bytes(b'corrupted')
            with self.assertRaises(ValueError):writer['installed_baseline'](original,p,plan['regions'])

    def test_writer_skips_identical_full_sectors(self):
        self.assertEqual(self.exercise_writer('unchanged'),[])

    def test_writer_orders_boot_last_and_reads_back(self):
        self.assertEqual(self.exercise_writer(),['metadata.bin','kernel.bin','dtb.bin','rootfs.cramfs','bootloader.bin'])

    def test_writer_rejects_changed_board_before_writing(self):
        self.assertEqual(self.exercise_writer('before'),[])
        self.assertEqual(self.exercise_writer('identity'),[])

    def test_writer_stops_on_readback_corruption(self):
        self.assertEqual(self.exercise_writer('after'),['metadata.bin'])

    def test_restore_backup_validation_survives_candidate_payload_changes(self):
        writer=runpy.run_path(str(ROOT/'flash.py'))
        plan=json.loads((ROOT/'build/plan.json').read_text())
        data=bytes([255])*0x800000
        report=dict(backup_sha256=hashlib.sha256(data).hexdigest(),regions=[])
        for r in plan['regions']:
            old=dict(r);old['sha256']='0'*64
            old['old_sector_sha256']=hashlib.sha256(data[r['erase_start']:r['erase_end']]).hexdigest()
            report['regions'].append(old)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'flash-8MiB.bin').write_bytes(data)
            (p/'review.json').write_text(json.dumps(report))
            (p/'security.txt').write_text('Flags: 0x00000000 \nSecure Boot: Disabled\nFlash Encryption: Disabled\nChip ID: 23\nBASE MAC: 02:00:00:00:00:01\n')
            with self.assertRaises(ValueError):writer['validate'](p)
            self.assertEqual(writer['validate'](p,restoring=True)[0],data)

    def test_security_state_rejection(self):
        check=runpy.run_path(str(ROOT/'flash.py'))['security_identity']
        good='Flags: 0x00000000 \nSecure Boot: Disabled\nFlash Encryption: Disabled\nChip ID: 23\nBASE MAC: 02:00:00:00:00:01\n'
        self.assertEqual(check(good),'02:00:00:00:00:01')
        for old,new in [('Flags: 0x00000000','Flags: 0x00000001'),('Secure Boot: Disabled','Secure Boot: Enabled'),('Chip ID: 23','Chip ID: 5')]:
            with self.assertRaises(ValueError):check(good.replace(old,new))

    def test_reconnect_open_retries_only_transient_access_errors(self):
        import types,errno
        helper=runpy.run_path(str(ROOT/'capture.py'))['open_after_reconnect']
        now=[0.0];calls=[0]
        def pause(seconds):now[0]+=seconds
        def opening():
            calls[0]+=1
            if calls[0]==1:raise PermissionError(errno.EACCES,'udev pending')
        port=types.SimpleNamespace(open=opening,close=lambda:None)
        result=helper(port,lambda:now[0],pause)
        self.assertEqual(result['open_attempts'],2)
        self.assertGreaterEqual(result['open_wait_seconds'],.2)
        def denied():raise PermissionError(errno.EACCES,'persistent')
        port.open=denied
        with self.assertRaises(RuntimeError):helper(port,lambda:now[0],pause)
        self.assertGreaterEqual(now[0],10)
        def busy():raise OSError(errno.EBUSY,'in use')
        port.open=busy;before=now[0]
        with self.assertRaises(OSError):helper(port,lambda:now[0],pause)
        self.assertEqual(now[0],before)

    def interactive_fixture(self):
        cap=runpy.run_path(str(ROOT/'capture.py'))
        fixtures=runpy.run_path(str(ROOT.parent/'tests/fixtures.py'))
        helpers=cap['session_tools']()
        boot=b'C5 FLASH BOOT v1\nC5 FLASH VERIFIED; JUMP entry=42000000 dt=427f0000\nLinux version 6.12\n'+fixtures['LEDGER']+fixtures['HEAD']
        done=fixtures['SHELL_LEDGER']+helpers['CHECKPOINT']+b'\n'
        return cap,helpers,boot,done

    def test_flash_interactive_requires_exit_ledger(self):
        cap,h,boot,done=self.interactive_fixture()
        self.assertEqual(cap['assess_interactive'](boot,h)[0],2)
        self.assertEqual(cap['assess_interactive'](boot+done,h)[0],0)
        self.assertEqual(cap['assess_interactive'](boot+done.replace(b'errors=00000000',b'errors=00000001'),h)[0],1)
        self.assertEqual(cap['assess_interactive'](boot+done+b'CPU_LOCKUP',h)[0],1)

    def exercise_interactive(self, mode):
        import io,types
        cap,h,boot,done=self.interactive_fixture();raw=bytearray(boot);now=[0.0];tx=io.BytesIO()
        class Port:
            buf=b''
            @property
            def in_waiting(self):return len(self.buf)
            def read(self,n):
                now[0]+=1
                if mode=='error':raise OSError('mock serial failure')
                out,self.buf=self.buf[:n],self.buf[n:];return out
            def write(self,data):
                if data==b'echo hello\n':self.buf+=b'echo hello\r\nhello\r\n/ # '
                elif data==b'\x15exit 0\n':self.buf+=done
                else:raise AssertionError(data)
                return len(data)
        class Terminal:
            closed=False;calls=0
            def __init__(self,stream):pass
            def read(self):
                self.calls+=1
                if mode=='timeout':return None
                return b'echo hello\n' if self.calls==1 else b'\x1d'
            def close(self):Terminal.closed=True
        def run():return cap['interact'](Port(),raw,raw.extend,tx,15,35,h,Terminal,lambda:now[0])
        if mode=='error':
            with self.assertRaises(OSError):run()
        else:
            self.assertEqual(run()[0],0)
            self.assertTrue(tx.getvalue().endswith(b'\x15exit 0\n'))
            if mode=='timeout':self.assertGreaterEqual(now[0],15)
            else:self.assertIn(b'hello',raw)
        self.assertTrue(Terminal.closed)

    def test_flash_terminal_manual_exit_and_cleanup(self):
        self.exercise_interactive('manual')
        self.exercise_interactive('error')

    def test_flash_terminal_timeout_requests_clean_exit(self):
        self.exercise_interactive('timeout')

    def test_power_cycle_gate_ignores_initial_session(self):
        gate=runpy.run_path(str(ROOT/'capture.py'))['CycleGate']()
        self.assertFalse(gate.update(True,0))
        self.assertFalse(gate.update(True,100))
        self.assertFalse(gate.update(False,101))
        self.assertFalse(gate.update(False,105))
        self.assertTrue(gate.update(True,107))
        self.assertEqual(gate.offline_seconds,6)
        gate=runpy.run_path(str(ROOT/'capture.py'))['CycleGate']()
        gate.update(False,1)
        with self.assertRaises(ValueError):gate.update(True,3)

    def test_usb_identity_follows_serial_across_port_names(self):
        import types
        check=runpy.run_path(str(ROOT/'capture.py'))['usb_identity']
        a=types.SimpleNamespace(vid=0x303a,pid=0x1001,serial_number='board',location='1-1',device='/dev/ttyACM0')
        b=types.SimpleNamespace(vid=0x303a,pid=0x1001,serial_number='board',location='1-2',device='/dev/ttyACM1')
        self.assertEqual(check(a),check(b))
        self.assertIsNone(check(types.SimpleNamespace(serial_number=None,location=None)))





    def exercise_init_test(self, missing_cpu=False, old_root=False, flash_read=False):
        import io
        cap,h,boot,done=self.interactive_fixture()
        boot=boot.replace(b'C5 USER ENTER',b'C5 ROOTFS READY: independent cramfs mounted read-only; persistence unavailable\nC5 INIT READY: proc and sysfs mounted; starting interactive shell\nC5 USER ENTER')
        raw=bytearray(boot);now=[0.0];nonce=b'0123456789abcdef'
        value=2166136261
        for byte in bytes([255])*64:value=((value^byte)*16777619)&0xffffffff
        if flash_read:raw[:0]=b'C5 FLASH READY id=174020 offset=00400000 bytes=00400000 write=0 sample_fnv='+f'{value:08x}'.encode()+b'\n'
        class Port:
            buf=b''
            @property
            def in_waiting(self):return len(self.buf)
            def read(self,n):
                now[0]+=.25
                out,self.buf=self.buf[:n],self.buf[n:];return out
            def write(self,data):
                if data==b'/bin/uname -a\n':self.buf+=b'Linux (none) 6.12.0 #1 riscv32 GNU/Linux\r\n/ # '
                elif data==b'/bin/cat /proc/cpuinfo\n':
                    self.buf+=b'/ # ' if missing_cpu else b'processor\t: 0\r\nisa\t: rv32imac_zicsr_zifencei\r\n/ # '
                elif data==b'/bin/cat /proc/mounts\n':
                    root=b'rootfs / rootfs rw 0 0\r\n' if old_root else b'mtd:rootfs / cramfs ro,relatime 0 0\r\n'
                    self.buf+=root+b'proc /proc proc rw 0 0\r\nsysfs /sys sysfs rw 0 0\r\n/ # '
                elif data.startswith(b"printf 'C5INIT:"):self.buf+=b'C5INIT:'+nonce+b':1\r\n/ # '
                elif data==b'/bin/cat /proc/mtd\n':self.buf+=b'mtd7: 00400000 00001000 "data"\r\n/ # '
                elif data.startswith(b'/bin/dd if=/dev/mtd7 '):self.buf+=b'C5READ:OK\r\n/ # '
                elif data.startswith(b'/bin/hexdump '):self.buf+=b'ff'*64+b'\r\n/ # '
                elif data==b'exit 0\n':self.buf+=done
                else:raise AssertionError(data)
                return len(data)
        tx=io.BytesIO()
        def run():return cap['init_session'](Port(),raw,raw.extend,tx,h,lambda:now[0],nonce,flash_read=flash_read,expected_data_fnv=value)
        if missing_cpu or old_root:
            with self.assertRaises(TimeoutError):run()
        else:
            self.assertEqual(run()[0],0)
            self.assertIn(b'/bin/cat /proc/mounts',tx.getvalue())

    def test_init_session_validates_actual_outputs(self):
        self.exercise_init_test()
        self.exercise_init_test(missing_cpu=True)
        self.exercise_init_test(old_root=True)
        self.exercise_init_test(flash_read=True)
        cap,h,boot,done=self.interactive_fixture()
        import io,types
        def unexpected(data):self.fail('No commands before init ready')
        self.assertEqual(cap['init_session'](types.SimpleNamespace(write=unexpected),bytearray(boot),lambda d:None,io.BytesIO(),h)[0],2)
        self.assertEqual(cap['assess'](b'C5 INIT FAIL: proc mount')[0],1)

    def test_rom_watchdog_loop_fails_before_bootloader(self):
        cap=runpy.run_path(str(ROOT/'capture.py'))
        raw=b'ESP-ROM:esp32c5-eco2\n' + b'rst:0x7 (TG0_WDT_HPSYS),boot:0x18 (SPI_FAST_FLASH_BOOT)\nCore0 Saved PC:0x40038598\n'*3
        self.assertEqual(cap['assess'](raw)[0],1)
        self.assertEqual(cap['assess'](b'rst:0x15 (USB_UART_HPSYS),boot:0x18 (SPI_FAST_FLASH_BOOT)\n')[0],2)

    def test_passive_capture_requires_flash_loader_sequence(self):
        check=runpy.run_path(str(ROOT/'capture.py'))['assess']
        raw=b'C5 FLASH BOOT v1\nC5 FLASH VERIFIED; JUMP entry=42000000 dt=427f0000\nLinux version 6.12\nhush - the humble shell\n/ # '
        self.assertEqual(check(raw)[0],0)
        self.assertEqual(check(raw.replace(b'C5 FLASH BOOT v1',b'C5 LOAD JUMP'))[0],2)
        self.assertEqual(check(raw+b'CPU_LOCKUP')[0],1)

    def test_pma_profiles_reject_wrong_range_lock_and_attributes(self):
        source=(ROOT/'main.c').read_text()
        helper=source[source.index('static int flash_pma_valid'):source.index('#define diagnostic_main')]
        code='#include <stdint.h>\n'+helper+r"""
int main(void){
 if(!flash_pma_valid(0xc0000011u,0x10bfffffu))return 1;
 if(!flash_pma_valid(0xc0000015u,0x10bfffffu))return 2;
 if((0xc0000011u|12u)!=0xc000001du || (0xc0000015u|12u)!=0xc000001du)return 3;
 if(flash_pma_valid(0xe0000015u,0x10bfffffu))return 4;
 if(flash_pma_valid(0xc0000015u,0x10afffffu))return 5;
 if(flash_pma_valid(0xc000001du,0x10bfffffu))return 6;
 if(flash_pma_valid(0xc8000015u,0x10bfffffu))return 7;
 return 0;}
"""
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'t.c').write_text(code)
            subprocess.run(['cc','-Wall','-Wextra','-Werror',str(p/'t.c'),'-o',str(p/'t')],check=True)
            subprocess.run([str(p/'t')],check=True)
        original=(ROOT.parent/'bootloader/psram/main.c').read_text()
        adapted=(ROOT/'build/psram-loader.inc').read_text()
        guard='if(cfg!=0xc0000011u||addr!=0x10bfffffu)fatal(9);'
        bounds='return !(off&63) && ((off<header[2]) || (off>=DT_OFF && off<DT_OFF+header[3]));'
        expanded=('return !(off&63) && ((off<header[2]) || (off>=DT_OFF && off<DT_OFF+header[3]) || '
                  '(header[1]==2 && header[6]>=64 && header[6]<=0xf0000u && !(header[6]&63) && '
                  'off>=0x700000u && off-0x700000u<=header[6]-64));')
        self.assertEqual(adapted,original.replace(guard,'if(!flash_pma_valid(cfg,addr))fatal(9);').replace(bounds,expanded))
        self.assertIn('if(cfg!=0xc000001du)fatal(10);',adapted)
        definitions=(ROOT.parent/'reference/esp-idf-v5.5.1/components/riscv/include/riscv/csr.h').read_text()
        for name,bit in [('PMA_R',4),('PMA_W',3),('PMA_X',2),('PMA_L',29)]:
            self.assertRegex(definitions,name+r'\s+BIT\('+str(bit)+r'\)')

    def test_watchdog_registers_match_pinned_headers(self):
        ref=ROOT.parent/'reference/esp-idf-v5.5.1/components'
        lp=(ref/'soc/esp32c5/register/soc/lp_wdt_reg.h').read_text()
        base=(ref/'soc/esp32c5/register/soc/reg_base.h').read_text()
        self.assertRegex(base,r'DR_REG_LP_WDT_BASE\s+0x600B1C00')
        for text in ['LP_WDT_WPROTECT_REG (DR_REG_LP_WDT_BASE + 0x18)',
            'LP_WDT_SWD_WPROTECT_REG (DR_REG_LP_WDT_BASE + 0x20)']:
            self.assertIn(text,lp)
        self.assertRegex(lp,r'LP_WDT_WDT_FLASHBOOT_MOD_EN\s+\(BIT\(12\)\)')
        self.assertRegex(lp,r'LP_WDT_SWD_AUTO_FEED_EN\s+\(BIT\(18\)\)')
        self.assertIn('0x50D83AA1',(ref/'hal/esp32c5/include/hal/lpwdt_ll.h').read_text())
if __name__=='__main__':unittest.main()
