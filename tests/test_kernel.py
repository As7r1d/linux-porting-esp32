#!/usr/bin/env python3
"""Host-only artifact and bounded USB writer checks; no hardware PASS claim."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
ROOT = Path(__file__).resolve().parent.parent/'kernel'
SRC = ROOT/'linux-6.12'
BUILD = ROOT/'build'

class Checks(unittest.TestCase):
    def test_entry_disassembly_and_trap_alignment(self):
        symbols = subprocess.check_output(['riscv64-unknown-elf-nm','-n',str(BUILD/'vmlinux')],text=True)
        syms = {x.split()[-1]:int(x.split()[0],16) for x in symbols.splitlines() if len(x.split())==3}
        self.assertEqual(syms['_start'],0x42000000)
        self.assertEqual(syms['c5_boot_trap'] % 64,0)
        self.assertLess(syms['_end'],0x42800000)
        dis = subprocess.check_output(['riscv64-unknown-elf-objdump','-d',str(BUILD/'arch/riscv/kernel/head.o')],text=True)
        self.assertNotRegex(dis,r'\b(?:mie|mip|pmpaddr0|pmpcfg0)\b')
        self.assertIn('mstatus',dis)
        self.assertIn('mtvec',dis)
        self.assertIn('earlycon-esp32c5.o',(BUILD/'drivers/tty/serial/.built-in.a.cmd').read_text())
        config=(BUILD/'.config').read_text()
        for line in ['CONFIG_ESP32C5_BOOTSTRAP=y','CONFIG_SERIAL_EARLYCON=y',
                     'CONFIG_PAGE_OFFSET=0x42000000','# CONFIG_MMU is not set',
                     '# CONFIG_SMP is not set','# CONFIG_FPU is not set']:
            self.assertIn(line+'\n',config)

    def test_memory_allocation_bounds_from_actual_c(self):
        if 'CONFIG_ESP32C5_MEMORY_CHECKPOINT=y' not in (BUILD/'.config').read_text():
            self.skipTest('First-banner configuration has no memory checkpoint')
        source=(SRC/'arch/riscv/kernel/c5-memory.c').read_text()
        function=source[source.index('static bool c5_allocation_valid('):source.index('void __init c5_memory_checkpoint')]
        code='''#include <stdbool.h>
#define C5_RAM_START 0x42000000UL
#define C5_RAM_END 0x42800000UL
#define C5_DT_START 0x427f0000UL
'''+function+'''
int main(void) {
 if(!c5_allocation_valid(0x42100000,4096,0x42100000))return 1;
 if(!c5_allocation_valid(0x427ef000,4096,0x42100000))return 2;
 if(c5_allocation_valid(0x420ff000,4096,0x42100000))return 3;
 if(c5_allocation_valid(0x427ef001,4096,0x42100000))return 4;
 if(c5_allocation_valid(0x427f0000,1,0x42100000))return 5;
 if(c5_allocation_valid(0x40800000,4096,0x42100000))return 6;
 if(c5_allocation_valid(0,4096,0x42100000))return 7;
 if(c5_allocation_valid(0x42100000,~0UL,0x42100000))return 8;
 if(c5_allocation_valid(0x42100000,0,0x42100000))return 9;
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'t.c').write_text(code)
            subprocess.run(['cc','-Wall','-Wextra','-Werror',str(p/'t.c'),'-o',str(p/'t')],check=True)
            subprocess.run([str(p/'t')],check=True)
        symbols=(BUILD/'symbols.txt').read_text()
        self.assertIn('c5_memory_checkpoint',symbols)

    def test_actual_alarm_classification_and_bounded_rearm(self):
        src=(SRC/'drivers/clocksource/timer-esp32c5.c').read_text()
        check=src[src.index('static int arm_check'):src.index('static int program(')]
        retry=src[src.index('static int rearm('):src.index('static int c5_shutdown')]
        code=r"""
#include <stdint.h>
#include <errno.h>
typedef uint64_t u64;typedef uint32_t u32;
#define PERIOD 160000ULL
#define ARM_MIN_LEAD (PERIOD/4)
static unsigned rearm_retries,late,attempts,scenario;
static u64 ticks=100000,last_target;
static u64 c5_systimer_cycles(void) {return ticks;}
static int program(u64 target) {
 last_target=target;++attempts;
 if(scenario==1)return attempts<3?-ETIME:0;
 if(scenario==2)return -EIO;
 if(scenario==3)return -EBUSY;
 if(scenario==4)return -ETIME;
 return 0;
}
"""+check+retry+r"""
int main(void) {
 if(arm_check(260000,100000,0,260000,0,0))return 1;
 if(arm_check(140000,100000,0,140000,0,0)!=-ETIME)return 2;
 if(arm_check(99999,100000,0,99999,0,0)!=-ETIME)return 3;
 if(arm_check(260000,100000,0,260001,0,0)!=-EIO)return 4;
 if(arm_check(260000,100000,0,260000,4,0)!=-EBUSY)return 5;
 if(arm_check(260000,100000,0,260000,0,1)!=-EIO)return 6;
 if(arm_check(0x10000000000000ULL,0,0,0,0,0)!=-EOVERFLOW)return 7;
 if(arm_check(0x100020000ULL,0xfffffff0ULL,1,0x20000,0,0))return 8;
 if(rearm(100001)||last_target!=260000||late!=1||attempts!=1)return 9;
 attempts=late=rearm_retries=0;scenario=1;
 if(rearm(260000)||attempts!=3||rearm_retries!=2||late!=2)return 10;
 attempts=late=rearm_retries=0;scenario=2;
 if(rearm(260000)!=-EIO||attempts!=1||rearm_retries)return 11;
 attempts=0;scenario=3;
 if(rearm(260000)!=-EBUSY||attempts!=1||rearm_retries)return 12;
 attempts=late=rearm_retries=0;scenario=4;
 if(rearm(260000)!=-ETIME||attempts!=3||rearm_retries!=3)return 13;
 return 0;
}
"""
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'t.c').write_text(code)
            subprocess.run(['cc','-Wall','-Wextra','-Werror',str(p/'t.c'),'-o',str(p/'t')],check=True)
            subprocess.run([str(p/'t')],check=True)

    def test_child_exec_psram_bounds(self):
        source=(SRC/'arch/riscv/kernel/c5-user.c').read_text()
        a=source.index('static bool c5_child_exec_valid')
        helper=source[a:source.index('#endif',a)]
        a=source.index('static bool in_ram')
        bounds=source[a:source.index('#ifdef CONFIG_ESP32C5_SHELL_CHECKPOINT',a)]
        code='#include <stdbool.h>\n#define RAM_LOW 0x42000000UL\n#define RAM_END 0x427f0000UL\n#define C5_USER_STACK_BYTES 32768UL\n#define SR_PIE 128UL\n'+bounds+helper+r"""
int main(void) {
 if(!c5_child_exec_valid(0x42100040,0x42118000,0x421000de,0x4212fff0,128))return 1;
 if(c5_child_exec_valid(0x42100040,0x42118000,0x42118000,0x4212fff0,128))return 2;
 if(c5_child_exec_valid(0x42100040,0x42118000,0x421000df,0x4212fff0,128))return 3;
 if(c5_child_exec_valid(0x42100040,0x42118000,0x421000de,0x42000100,128))return 4;
 if(c5_child_exec_valid(0x42100040,0x42118000,0x421000de,0x4212fff0,0x1880))return 5;
 if(c5_child_exec_valid(0x42100040,0x42118000,0x421000de,0x42800000,128))return 6;
 return 0;
}
"""
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'t.c').write_text(code)
            subprocess.run(['cc','-Wall','-Wextra','-Werror',str(p/'t.c'),'-o',str(p/'t')],check=True)
            subprocess.run([str(p/'t')],check=True)

    def test_clic_signal_return_state_and_syscall_detection(self):
        source=(SRC/'arch/riscv/kernel/signal.c').read_text()
        first=source.index('static unsigned long c5_signal_no_restart')
        helpers=source[first:source.index('#endif',first)]
        code='#include <stdbool.h>\n#define EXC_SYSCALL 8\n'+helpers+r"""
int main(void) {
 unsigned long c=c5_signal_no_restart();
 if(c!=0x08000000UL || (c & 0x30000000UL) || !(c & 0x08000000UL))return 1;
 if(c5_signal_syscall(c) || c5_signal_syscall(~0UL))return 2;
 if(!c5_signal_syscall(0x08000008UL))return 3;
 if(c5_signal_syscall(0x88000017UL) || c5_signal_syscall(0x08000002UL))return 4;
 return 0;
}
"""
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'t.c').write_text(code)
            subprocess.run(['cc','-Wall','-Wextra','-Werror',str(p/'t.c'),'-o',str(p/'t')],check=True)
            subprocess.run([str(p/'t')],check=True)

    def test_usb_reset_policy_preserves_other_bits_and_status_flags(self):
        source=(SRC/'drivers/tty/serial/esp32c5-usb.c').read_text()
        start=source.index('static u32 c5_usb_reset_control')
        helper=source[start:source.index('static int c5_probe',start)]
        code='#include <stdint.h>\ntypedef uint32_t u32;\n#define BIT(n) (1u<<(n))\n'+helper+r"""
int main(void){
 if(c5_usb_reset_control(0)!=4)return 1;
 if(c5_usb_reset_control(3)!=4)return 2;
 if(c5_usb_reset_control(0x1003)!=0x1004)return 3;
 if(c5_usb_reset_control(4)!=4)return 4;
 return 0;}
"""
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'t.c').write_text(code)
            subprocess.run(['cc','-Wall','-Wextra','-Werror',str(p/'t.c'),'-o',str(p/'t')],check=True)
            subprocess.run([str(p/'t')],check=True)
        reg=(ROOT.parent/'reference/esp-idf-v5.5.1/components/soc/esp32c5/register/soc/usb_serial_jtag_reg.h').read_text()
        self.assertIn('USB_SERIAL_JTAG_CHIP_RST_REG (DR_REG_USB_SERIAL_JTAG_BASE + 0x4c)',reg)
        self.assertRegex(reg,r'USB_SERIAL_JTAG_USB_UART_CHIP_RST_DIS\s+\(BIT\(2\)\)')

if __name__=='__main__':unittest.main()
