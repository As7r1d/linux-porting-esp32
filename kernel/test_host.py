#!/usr/bin/env python3
"""Host-only artifact and bounded USB writer checks; no hardware PASS claim."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
ROOT = Path(__file__).resolve().parent
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

    def test_irq_entry_and_deadline_bounds(self):
        if 'CONFIG_ESP32C5_IRQ_CHECKPOINT=y' not in (BUILD/'.config').read_text():
            self.skipTest('IRQ checkpoint not configured')
        syms={line.split()[-1]:int(line.split()[0],16) for line in (BUILD/'symbols.txt').read_text().splitlines() if len(line.split())==3}
        self.assertEqual(syms['handle_exception']%64,0)
        self.assertIn('c5_irq_checkpoint',syms)
        dis=subprocess.check_output(['riscv64-unknown-elf-objdump','-d','--disassemble=ret_from_exception',str(BUILD/'vmlinux')],text=True)
        self.assertLess(dis.index('mcause'),dis.index('mstatus'))
        self.assertIn('mret',dis)
        src=(SRC/'arch/riscv/kernel/c5-timer-irq.c').read_text()
        pure=src[src.index('static bool valid_deadline'):src.index('static bool arm(')]
        code='#include <stdbool.h>\n#include <stdint.h>\ntypedef uint64_t u64;\n'+pure+'''
int main(void) {
 if(!valid_deadline(100,110,65636))return 1;
 if(!valid_deadline(0xfffffff0ULL,0x100000000ULL,0x10000fff0ULL))return 2;
 if(valid_deadline(100,99,65636))return 3;
 if(valid_deadline(100,65636,65636))return 4;
 if(valid_deadline(100,64612,65636))return 5;
 if(valid_deadline(0xffffffffff000ULL,0xffffffffff001ULL,0x1000000000f000ULL))return 6;
 if(valid_deadline(100,110,70000))return 7;
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'t.c').write_text(code)
            subprocess.run(['cc','-Wall','-Wextra','-Werror',str(p/'t.c'),'-o',str(p/'t')],check=True)
            subprocess.run([str(p/'t')],check=True)

    def test_timekeeping_source_and_actual_clock_guards(self):
        if 'CONFIG_ESP32C5_TIME_CHECKPOINT=y' not in (BUILD/'.config').read_text():
            self.skipTest('Time checkpoint not configured')
        dis=subprocess.check_output(['riscv64-unknown-elf-objdump','-d','--disassemble=clocksource_default_clock',str(BUILD/'vmlinux')],text=True)
        self.assertIn('<c5_cs>',dis)
        delay=subprocess.check_output(['riscv64-unknown-elf-objdump','-d','--disassemble=__delay',str(BUILD/'vmlinux')],text=True)
        self.assertIn('c5_systimer_cycles',delay)
        self.assertNotIn('clint_time_val',delay)
        self.assertIn('CONFIG_HZ=100\n',(BUILD/'.config').read_text())
        src=(SRC/'drivers/clocksource/timer-esp32c5.c').read_text()
        funcs=src[src.index('static bool valid_clock_config'):src.index('void __init c5_clock_prepare')]
        funcs+=src[src.index('static bool valid_elapsed'):src.index('void __init __noreturn c5_tick_checkpoint')]
        code='''#include <stdbool.h>
#include <stdint.h>
typedef uint32_t u32;typedef uint64_t u64;
#define BIT(n) (1U<<(n))
#define div_u64(n,d) ((n)/(d))
'''+funcs+'''
int main(void) {
 if(!valid_clock_config(1U<<22,40)||!valid_clock_config(1U<<22,48))return 1;
 if(valid_clock_config((1U<<22)|(1U<<20),40)||valid_clock_config(0,40)||valid_clock_config(1U<<22,26))return 2;
 if(!valid_elapsed(320000000,5120000))return 3;
 if(valid_elapsed(320000000,0)||valid_elapsed(320000000,~0ULL))return 4;
 if(valid_elapsed(1000000,16000)||valid_elapsed(2000000000,32000000))return 5;
 if(valid_elapsed(320000000,6000000))return 6;
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'t.c').write_text(code)
            subprocess.run(['cc','-Wall','-Wextra','-Werror',str(p/'t.c'),'-o',str(p/'t')],check=True)
            subprocess.run([str(p/'t')],check=True)

    def test_sched_clock_irq_enable_is_remasked_before_checkpoint(self):
        if 'CONFIG_ESP32C5_TIME_CHECKPOINT=y' not in (BUILD/'.config').read_text():
            self.skipTest('Time checkpoint not configured')
        if 'CONFIG_ESP32C5_SCHED_CHECKPOINT=y' in (BUILD/'.config').read_text():
            self.skipTest('Scheduler variant uses normal IRQ-enabled boot ordering')
        dis=lambda name:subprocess.check_output(['riscv64-unknown-elf-objdump','-d','--disassemble='+name,str(BUILD/'vmlinux')],text=True)
        self.assertRegex(dis('sched_clock_init'),r'\bcsrsi\s+mstatus,8')
        body=dis('start_kernel')
        begin=body.index('<sched_clock_init>')
        end=body.index('<c5_tick_checkpoint>',begin)
        segment=body[begin:end]
        self.assertRegex(segment,r'\bcsrci\s+mstatus,8')
        self.assertNotRegex(segment,r'\bcsrsi\s+mstatus,8')

    def test_scheduler_variant_hooks_and_context_switch_code(self):
        config=(BUILD/'.config').read_text()
        if 'CONFIG_ESP32C5_SCHED_CHECKPOINT=y' not in config:
            self.skipTest('Scheduler checkpoint not configured')
        dis=lambda name:subprocess.check_output(['riscv64-unknown-elf-objdump','-d','--disassemble='+name,str(BUILD/'vmlinux')],text=True)
        body=dis('start_kernel')
        self.assertIn('<c5_sched_clock_start>',body)
        self.assertNotIn('<c5_tick_checkpoint>',body)
        tail=body[body.index('<c5_sched_clock_start>'):]
        self.assertRegex(tail,r'\bcsrsi\s+mstatus,8')
        if 'CONFIG_ESP32C5_USER_CHECKPOINT=y' in config:
            self.assertNotIn('<c5_sched_checkpoint>',dis('kernel_init'))
            self.assertIn('<kernel_init_freeable>',dis('kernel_init'))
        else:
            self.assertIn('<c5_sched_checkpoint>',dis('kernel_init'))
            self.assertNotIn('<kernel_init_freeable>',dis('kernel_init'))
        self.assertIn('schedule_tail',dis('ret_from_fork'))
        self.assertRegex(dis('__switch_to'),r'\bmv\s+tp,a1')
        self.assertIn('CONFIG_PREEMPT_NONE=y\n',config)
        self.assertIn('nohlt',config)

    def test_user_entry_hooks_and_post_init_lifetime(self):
        if 'CONFIG_ESP32C5_USER_CHECKPOINT=y' not in (BUILD/'.config').read_text():
            self.skipTest('User checkpoint not configured')
        dis=lambda name:subprocess.check_output(['riscv64-unknown-elf-objdump','-d','--disassemble='+name,str(BUILD/'vmlinux')],text=True)
        self.assertIn('<c5_user_prepare>',dis('start_thread'))
        self.assertIn('<c5_user_sys_enter>',dis('do_trap_ecall_u'))
        self.assertIn('<c5_user_sys_return>',dis('do_trap_ecall_u'))
        self.assertIn('<c5_user_exit>',dis('do_exit'))
        table=subprocess.check_output(['riscv64-unknown-elf-objdump','-t',str(BUILD/'vmlinux')],text=True)
        for name in ['c5_user_prepare','c5_user_sys_enter','c5_user_sys_return','c5_user_exit','c5_sched_clock_stop']:
            line=next(line for line in table.splitlines() if line.split() and line.split()[-1]==name)
            self.assertEqual(line.split()[3],'.text',name)
        for name in ['__riscv_sys_getpid','__riscv_sys_openat','__riscv_sys_write','__riscv_sys_clock_nanosleep','__riscv_sys_exit_group']:
            self.assertIn(name,table)
        source=(SRC/'arch/riscv/kernel/c5-user.c').read_text()
        pure=source[source.index('static bool in_ram'):source.index('void c5_user_prepare')]
        code='#include <stdbool.h>\n#define RAM_LOW 0x42000000UL\n#define RAM_END 0x427f0000UL\n'+pure+"""
int main(void) {
 if(!in_ram(0x42000000,0x427f0000))return 1;
 if(in_ram(0x40800000,0x40801000))return 2;
 if(in_ram(0x427f0000,0x42800000))return 3;
 if(in_ram(0x42100000,0x42100000)||in_ram(0x42100000,0))return 4;
 return 0;
}
"""
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'t.c').write_text(code)
            subprocess.run(['cc','-Wall','-Wextra','-Werror',str(p/'t.c'),'-o',str(p/'t')],check=True)
            subprocess.run([str(p/'t')],check=True)

    def test_polling_tty_fifo_budget_and_console_backpressure(self):
        if 'CONFIG_ESP32C5_TTY_CHECKPOINT=y' not in (BUILD/'.config').read_text():
            self.skipTest('TTY checkpoint not configured')
        symbols=(BUILD/'symbols.txt').read_text()
        self.assertIn('c5_tty_stats',symbols)
        self.assertIn('c5_probe',symbols)
        source=(SRC/'drivers/tty/serial/esp32c5-usb.c').read_text()
        rx=source[source.index('            for (unsigned int i=0;'):source.index('        if (tx_enabled)')]
        rx=rx[:rx.rfind('        }')]
        put=source[source.index('static bool tx_ready'):source.index('static void c5_stop_tx')]
        put+=source[source.index('static void c5_putchar'):source.index('static void c5_console_write')]
        code=r"""
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
struct uart_port { unsigned char *membase; struct { unsigned rx,tx; } icount; };
static unsigned index_rx, inserted, status_reads, writes, ready_after, mode;
static uint32_t readl(void *p) {
 if(p==(void*)0x6000f004UL) {
  ++status_reads;
  if(mode==0)return index_rx<100 ? 4:0;
  return status_reads>=ready_after?2:0;
 }
 if(p!=(void*)0x6000f000UL || mode || index_rx>=100)abort();
 return index_rx++;
}
static void writel(uint32_t v,void *p) {
 if(writes==0 && (p!=(void*)0x6000f000UL || v!='A'))abort();
 if(writes==1 && (p!=(void*)0x6000f004UL || v!=1))abort();
 if(++writes>2)abort();
}
#define TTY_NORMAL 0
static void uart_insert_char(struct uart_port *p,unsigned s,unsigned o,unsigned c,unsigned f) {
 if(c!=inserted++)abort();
}
static void cpu_relax(void) {}
"""+put+'static bool rx_once(struct uart_port *p) { bool push=false;\n'+rx+'return push; }\n'+r"""
int main(void) {
 struct uart_port p={.membase=(void*)0x6000f000UL};
 if(!rx_once(&p)||index_rx!=64||p.icount.rx!=64||inserted!=64)return 1;
 if(!rx_once(&p)||index_rx!=100||p.icount.rx!=100||inserted!=100)return 2;
 if(rx_once(&p)||index_rx!=100)return 3;
 mode=1;status_reads=0;ready_after=3;c5_putchar(&p,'A');
 if(status_reads!=3||writes!=2)return 4;
 status_reads=writes=0;ready_after=1000001;c5_putchar(&p,'A');
 if(status_reads!=1000000||writes)return 5;
 return 0;
}
"""
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'t.c').write_text(code)
            subprocess.run(['cc','-Wall','-Wextra','-Wno-unused-parameter','-Werror',str(p/'t.c'),'-o',str(p/'t')],check=True)
            subprocess.run([str(p/'t')],check=True)

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

    def test_libc_context_bounds_and_call_budget(self):
        if 'CONFIG_ESP32C5_LIBC_CHECKPOINT=y' not in (BUILD/'.config').read_text():
            self.skipTest('Libc checkpoint not configured')
        src=(SRC/'arch/riscv/kernel/c5-libc.c').read_text()
        func=src[src.index('static bool libc_context_valid'):src.index('void c5_libc_sys_enter')]
        code='#include <stdbool.h>\n#define C5_USER_STACK_BYTES 32768UL\n#define TRACE_MAX 4096U\n'+func+r"""
int main(void) {
 if(!libc_context_valid(0x42100044,0x42107ff0,0x42100040,0x42107000,0x42108000,0x08000008,1,0))return 1;
 if(libc_context_valid(0x42107000,0x42107ff0,0x42100040,0x42107000,0x42108000,8,1,0))return 2;
 if(libc_context_valid(0x42100044,0x42108010,0x42100040,0x42107000,0x42108000,8,1,0))return 3;
 if(libc_context_valid(0x42100044,0x42107ff1,0x42100040,0x42107000,0x42108000,8,1,0))return 4;
 if(libc_context_valid(0x42100044,0x42107ff0,0x42100040,0x42107000,0x42108000,0x88000017,1,0))return 5;
 if(libc_context_valid(0x42100044,0x42107ff0,0x42100040,0x42107000,0x42108000,8,0,0))return 6;
 if(libc_context_valid(0x42100044,0x42107ff0,0x42100040,0x42107000,0x42108000,8,1,TRACE_MAX))return 7;
 return 0;
}
"""
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'t.c').write_text(code)
            subprocess.run(['cc','-Wall','-Wextra','-Werror',str(p/'t.c'),'-o',str(p/'t')],check=True)
            subprocess.run([str(p/'t')],check=True)
        dis=lambda n:subprocess.check_output(['riscv64-unknown-elf-objdump','-d','--disassemble='+n,str(BUILD/'vmlinux')],text=True)
        self.assertIn('<c5_libc_sys_enter>',dis('c5_user_sys_enter'))
        self.assertIn('<c5_libc_sys_return>',dis('c5_user_sys_return'))
        self.assertIn('<c5_libc_exit>',dis('c5_user_exit'))

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

    def test_actual_usb_writer_bounded_and_ordered(self):
        source=(SRC/'drivers/tty/serial/earlycon-esp32c5.c').read_text()
        func=source[source.index('static void c5_usb_putc'):source.index('static void c5_usb_write')]
        harness=r'''
#include <stdint.h>
#include <stdlib.h>
struct uart_port { int unused; };
#define C5_EP1 ((void*)0x6000f000UL)
#define C5_EP1_CONF ((void*)0x6000f004UL)
static unsigned reads,writes,relaxes,ready_after;
static uint32_t readl(void *p) {
 if(p!=C5_EP1_CONF) abort();
 return ++reads>=ready_after ? 2 : 0;
}
static void writel(uint32_t v,void *p) {
 if(writes==0 && (p!=C5_EP1 || v!='A')) abort();
 if(writes==1 && (p!=C5_EP1_CONF || v!=1)) abort();
 if(++writes>2) abort();
}
static void cpu_relax(void) {++relaxes;}
'''+func+r'''
int main(void) {
 struct uart_port p={0};ready_after=3;c5_usb_putc(&p,'A');
 if(reads!=3||writes!=2||relaxes!=2)return 1;
 reads=writes=relaxes=0;ready_after=1000001;c5_usb_putc(&p,'A');
 if(reads!=1000000||writes||relaxes!=1000000)return 2;
 return 0;
}
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'test.c').write_text(harness)
            subprocess.run(['cc','-O2','-Wall','-Wextra','-Wno-unused-parameter','-Werror',str(p/'test.c'),'-o',str(p/'test')],check=True)
            subprocess.run([str(p/'test')],check=True)

if __name__=='__main__': unittest.main()
