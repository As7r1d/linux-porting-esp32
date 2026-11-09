// SPDX-License-Identifier: GPL-2.0
/* Diagnostic only: all PSRAM is accessible to one PID1, no isolation claim. */
#include <linux/kernel.h>
#include <linux/sched.h>
#include <linux/sched/mm.h>
#include <linux/mm.h>
#include <linux/io.h>
#include <linux/irqflags.h>
#include <linux/timekeeping.h>
#include <asm/csr.h>
#include <asm/ptrace.h>
#include <asm/c5-bootstrap.h>

/* Opt-in process tracing; diagnostic accounting and failure checks stay active. */
bool c5_trace_enabled;
static int __init c5_trace_setup(char *value)
{
    return kstrtobool(value, &c5_trace_enabled);
}
early_param("c5_trace", c5_trace_setup);

#define HP ((void __iomem *)0x60099000UL)
#define RAM_LOW 0x42000000UL
#define RAM_END 0x427f0000UL
static unsigned long code_low, code_end, stack_top, pmp_cfg, pmp_addr;
static unsigned int entered, calls, returns, errors, current_nr;
static unsigned int hp_before, hp_during;
static long kmsg_fd = -1;
static u64 sleep_begin, sleep_ns;
#ifdef CONFIG_ESP32C5_TTY_CHECKPOINT
static const unsigned int sequence[] = {172, 56, 64, 63, 64, 29, 57, 94};
#else
static const unsigned int sequence[] = {172, 56, 64, 0x7fffffff, 407, 172, 57, 94};
#endif
static inline void sync_io(void) { asm volatile("fence iorw,iorw" ::: "memory"); }
static bool in_ram(unsigned long start, unsigned long end)
{
    return start >= RAM_LOW && start < end && end <= RAM_END;
}
#ifdef CONFIG_ESP32C5_SHELL_CHECKPOINT
static bool c5_child_exec_valid(unsigned long low, unsigned long end,
    unsigned long pc, unsigned long sp, unsigned long status)
{
    return in_ram(low,end) && in_ram(sp-C5_USER_STACK_BYTES,sp) &&
        pc>=low && pc<end && !(pc&1) && status==SR_PIE;
}
#endif
void c5_user_prepare(struct pt_regs *regs)
{
    unsigned long flags, first, last;
    int (*wb)(unsigned int, unsigned int) = (void *)0x40000658;
    int (*inv)(unsigned int, unsigned int) = (void *)0x40000650;
#ifdef CONFIG_ESP32C5_SHELL_CHECKPOINT
    if (entered && current->mm) {
        unsigned long low=current->mm->start_code, end=current->mm->end_code;
        if (!c5_child_exec_valid(low,end,regs->epc,regs->sp,regs->status))
            panic("C5 USER child exec bounds");
        first=low & ~63UL; last=ALIGN(end,64);
        if (wb(first,last-first) || inv(first,last-first))
            panic("C5 USER child instruction cache maintenance");
        asm volatile("fence rw,rw; fence.i" ::: "memory");
        if (task_pid_nr(current)==1) {
            /* PID1 /init execs the final interactive shell. Preserve the
             * original protection snapshot and syscall accounting. */
            code_low=low;code_end=end;stack_top=regs->sp;
        }
        regs->cause=0x08000000; regs->a0=0;
        c5_trace("C5 USER EXEC pid=%d entry=%08lx end=%08lx stack=%08lx\n",
            task_pid_nr(current),regs->epc,end,regs->sp);
        return;
    }
#endif
    if (entered || task_pid_nr(current) != 1 || !current->mm)
        panic("C5 USER prepare identity");
    code_low = current->mm->start_code; code_end = current->mm->end_code;
    stack_top = regs->sp;
    if (!in_ram(code_low, code_end) || !in_ram(stack_top - C5_USER_STACK_BYTES, stack_top) ||
        regs->epc < code_low || regs->epc >= code_end || (regs->epc & 1) || regs->status != SR_PIE)
        panic("C5 USER image/stack bounds");
    local_irq_save(flags);
    pmp_cfg = csr_read(CSR_PMPCFG0); pmp_addr = csr_read(CSR_PMPADDR0);
    hp_before = readl(HP + 0xc);
    pr_notice("C5 USER PROTECT BASE hp_en=%08x hp_start=%08x hp_end=%08x hp_attr=%08x hp_func=%08x pmp=%08lx addr=%08lx\n",
        readl(HP), readl(HP+4), readl(HP+8), hp_before, readl(HP+0xc4), pmp_cfg, pmp_addr);
    /* Preserve filter bounds, enable state and locks. Accept only ROM baseline. */
    if (pmp_cfg || csr_read(0x3a1) || csr_read(0x3a2) ||
        csr_read(0x3a3) || readl(HP) != 1 || readl(HP+4) ||
        readl(HP+8) != 0xffffffffU || hp_before || readl(HP+0xc4) != 0x1f)
        panic("C5 USER protection baseline");
    writel(hp_before | 7, HP+0xc); sync_io(); hp_during = readl(HP+0xc);
    csr_write(CSR_PMPADDR0, 0x108fffffUL); /* NAPOT 8 MiB at 0x42000000 */
    csr_write(CSR_PMPCFG0, 0x1f); /* unlocked RWX entry0; rest OFF */
    if (hp_during != 7 || csr_read(CSR_PMPCFG0) != 0x1f ||
        csr_read(CSR_PMPADDR0) != 0x108fffffUL)
        panic("C5 USER protection readback");
    first = code_low & ~63UL; last = ALIGN(code_end, 64);
    /* No cache disable while executing from PSRAM. Clean newly loaded code. */
    if (wb(first, last-first) || inv(first, last-first))
        panic("C5 USER instruction cache maintenance");
    asm volatile("fence rw,rw; fence.i" ::: "memory");
    /* CLIC mcause restoration must carry MPP=U, MPIE=1, MPIL=0. */
    regs->cause = 0x08000000;
#ifdef CONFIG_ESP32C5_LIBC_CHECKPOINT
    regs->a0 = 0; /* static crt1 rtld_fini ABI: no dynamic loader finalizer */
#endif
    entered = 1;
    pr_notice("C5 USER ENTER entry=%08lx code_end=%08lx stack=%08lx status=%08lx cause=%08lx hp=%08x pmp=%08lx\n",
        regs->epc, code_end, stack_top, regs->status, regs->cause, hp_during, csr_read(CSR_PMPCFG0));
    local_irq_restore(flags);
}
void c5_user_sys_enter(struct pt_regs *regs)
{
#ifdef CONFIG_ESP32C5_LIBC_CHECKPOINT
    c5_libc_sys_enter(regs,code_low,code_end,stack_top,entered);
    return;
#endif
    if (task_pid_nr(current) != 1) panic("C5 USER unexpected task");
    current_nr = regs->a7;
    if (!entered || calls >= ARRAY_SIZE(sequence) || current_nr != sequence[calls] ||
        (regs->cause & 0x80000fffUL) != 8 || !user_mode(regs) ||
        regs->epc < code_low || regs->epc >= code_end ||
        regs->sp < stack_top - C5_USER_STACK_BYTES || regs->sp > stack_top || (regs->sp & 15))
        errors |= 1;
    ++calls;
#ifdef CONFIG_ESP32C5_TTY_CHECKPOINT
    if (current_nr == 64 && ((long)regs->a0 != kmsg_fd ||
        regs->a2 != (calls == 3 ? 13 : 30))) errors |= 2;
    if (current_nr == 63) {
        if ((long)regs->a0 != kmsg_fd || regs->a2 != 64) errors |= 2;
        sleep_begin = ktime_get_ns();
    }
#else
    if (current_nr == 64 && ((long)regs->a0 != kmsg_fd || regs->a2 != 34)) errors |= 2;
    if (current_nr == 407) sleep_begin = ktime_get_ns();
#endif
}
void c5_user_sys_return(struct pt_regs *regs)
{
#ifdef CONFIG_ESP32C5_LIBC_CHECKPOINT
    c5_libc_sys_return(regs);
    return;
#endif
    long value = regs->a0;
    ++returns;
    switch (current_nr) {
    case 172: if (value != 1) errors |= 4; break;
    case 56: kmsg_fd = value; if (value < 0) errors |= 8; break;
#ifdef CONFIG_ESP32C5_TTY_CHECKPOINT
    case 64: if (value != (calls == 3 ? 13 : 30)) errors |= 16; break;
    case 63:
        sleep_ns = ktime_get_ns() - sleep_begin;
        if (value != 20 || sleep_ns > 25000000000ULL) errors |= 64;
        break;
    case 29: if (value) errors |= 32; break;
#else
    case 64: if (value != 34) errors |= 16; break;
#endif
    case 0x7fffffff: if (value != -ENOSYS) errors |= 32; break;
    case 407:
        sleep_ns = ktime_get_ns() - sleep_begin;
        if (value || sleep_ns < 20000000ULL || sleep_ns > 2000000000ULL) errors |= 64;
        break;
    case 57: if (value) errors |= 128; break;
    default: errors |= 256;
    }
}
void __noreturn c5_user_exit(long code)
{
    struct c5_clock_stats clock;
    unsigned int restored, result;
#ifdef CONFIG_ESP32C5_TTY_CHECKPOINT
    unsigned int rx, tx, polls;
#endif
    c5_sched_clock_stop(&clock);
    /* Terminal checkpoint: never return to U after revoking permissions. */
    csr_write(CSR_PMPCFG0, pmp_cfg); csr_write(CSR_PMPADDR0, pmp_addr);
    writel(hp_before, HP+0xc); sync_io();
    restored = csr_read(CSR_PMPCFG0) == pmp_cfg && csr_read(CSR_PMPADDR0) == pmp_addr &&
               readl(HP+0xc) == hp_before;
#ifdef CONFIG_ESP32C5_LIBC_CHECKPOINT
    c5_libc_exit(code,&clock,restored);
#endif
    result = code || !entered || calls != 8 || returns != 7 || errors || !restored ||
        clock.irqs < 2 || clock.irqs >= 4096 || clock.irqs != clock.callbacks ||
        clock.irqs != clock.acked || clock.irqs != clock.jiffies_delta || clock.faults ||
        !clock.source_off || !irqs_disabled() || (csr_read(0xfb1) & 0xff000000UL);
#ifdef CONFIG_ESP32C5_TTY_CHECKPOINT
    c5_tty_stats(&rx, &tx, &polls);
    if (rx != 20 || tx < 43 || !polls) result |= 2;
    pr_notice("C5 TTY v1 result=%08x pid=%08x entered=%08x calls=%08x returns=%08x errors=%08x exit_code=%08lx wait_ns=%016llx rx_bytes=%08x tx_bytes=%08x polls=%08x protection_restored=%08x irq_count=%08x callbacks=%08x acked=%08x jiffies_delta=%08lx clock_faults=%08x source_off=%08x irq_off=%08x level_after=%08lx\n",
        result, task_pid_nr(current), entered, calls, returns, errors, code, sleep_ns,
        rx, tx, polls, restored, clock.irqs, clock.callbacks, clock.acked,
        clock.jiffies_delta, clock.faults, clock.source_off, irqs_disabled(), csr_read(0xfb1)&0xff000000UL);
    if (result) pr_emerg("C5 TTY FAIL: console/read/write/context checks\n");
    else pr_notice("C5 TTY CHECKPOINT: PID1 console nonce round-trip returned; intentional halt at diagnostic exit\n");
#else
    pr_notice("C5 USER v1 result=%08x pid=%08x entered=%08x calls=%08x returns=%08x errors=%08x exit_code=%08lx sleep_ns=%016llx code_start=%08lx code_end=%08lx stack=%08lx hp_before=%08x hp_during=%08x protection_restored=%08x irq_count=%08x callbacks=%08x acked=%08x jiffies_delta=%08lx clock_faults=%08x source_off=%08x irq_off=%08x level_after=%08lx\n",
        result, task_pid_nr(current), entered, calls, returns, errors, code, sleep_ns,
        code_low, code_end, stack_top, hp_before, hp_during, restored, clock.irqs,
        clock.callbacks, clock.acked, clock.jiffies_delta, clock.faults,
        clock.source_off, irqs_disabled(), csr_read(0xfb1) & 0xff000000UL);
    if (result) pr_emerg("C5 USER FAIL: PID1/syscall/sleep/context checks\n");
    else pr_notice("C5 USER CHECKPOINT: PID1 U-mode syscalls and sleep returned; intentional halt at diagnostic exit\n");
#endif
    for (;;) cpu_relax();
}
