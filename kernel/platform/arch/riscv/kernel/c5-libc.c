// SPDX-License-Identifier: GPL-2.0
/* Observe libc syscalls without prescribing libc's startup call order. */
#include <linux/kernel.h>
#include <linux/sched.h>
#include <linux/pid.h>
#include <linux/irqflags.h>
#include <linux/swap.h>
#include <linux/signal.h>
#include <linux/uaccess.h>
#include <uapi/linux/wait.h>
#include <asm/csr.h>
#include <asm/ptrace.h>
#include <asm/c5-bootstrap.h>
#ifdef CONFIG_ESP32C5_SHELL_CHECKPOINT
#define TRACE_MAX 4096U
#else
#define TRACE_MAX 128U
#endif
static struct { unsigned int nr; long value; unsigned long pc; } trace[128];
static unsigned int calls, returns, seen, errors, enosys, nr, entered;
static unsigned long arg0, arg1, pc;
static bool active;
#ifdef CONFIG_ESP32C5_SHELL_CHECKPOINT
static unsigned long wait_info, wait_options;
static unsigned int reap_samples;
#endif
static bool libc_context_valid(unsigned long epc, unsigned long sp,
    unsigned long low, unsigned long end, unsigned long top,
    unsigned long cause, bool user, unsigned int count)
{
    return user && (cause & 0x80000fffUL) == 8 && count < TRACE_MAX &&
        epc >= low && epc < end && !(epc & 1) && sp <= top &&
        sp >= top - C5_USER_STACK_BYTES && !(sp & 15);
}
void c5_libc_sys_enter(struct pt_regs *regs, unsigned long low,
    unsigned long end, unsigned long top, unsigned int prepared)
{
#ifdef CONFIG_ESP32C5_SHELL_CHECKPOINT
    if (regs->a7 == 220 || regs->a7 == 221 || regs->a7 == 260 ||
        regs->a7 == 93 || regs->a7 == 94)
        c5_trace("C5 PROC ENTER pid=%d nr=%lu pc=%08lx sp=%08lx a0=%08lx a1=%08lx\n",
            task_pid_nr(current),regs->a7,regs->epc,regs->sp,regs->a0,regs->a1);
#endif
#ifdef CONFIG_ESP32C5_SHELL_CHECKPOINT
    /* Observe PID1 only: a vfork child must not overwrite its pending clone. */
    if (task_pid_nr(current) != 1) return;
    /* rt_sigreturn executes the kernel-generated NOMMU stack trampoline. */
    if (regs->a7 == 139) { low=top-C5_USER_STACK_BYTES; end=top; }
#endif
#ifdef CONFIG_ESP32C5_SHELL_CHECKPOINT
    if (regs->a7 == 95) { wait_info=regs->a2; wait_options=regs->a3; }
#endif
    entered = prepared;
    if (!prepared || task_pid_nr(current) != 1 || active ||
        !libc_context_valid(regs->epc,regs->sp,low,end,top,regs->cause,user_mode(regs),calls))
        errors |= 1;
    nr=regs->a7; arg0=regs->a0; arg1=regs->a1; pc=regs->epc;
    active=true; ++calls;
}
void c5_libc_sys_return(struct pt_regs *regs)
{
    long value=regs->a0;
#ifdef CONFIG_ESP32C5_SHELL_CHECKPOINT
    if (task_pid_nr(current) != 1) return;
#endif
#ifdef CONFIG_ESP32C5_SHELL_CHECKPOINT
    if (nr == 220 || nr == 221 || nr == 260)
        c5_trace("C5 PROC RETURN pid=%d observed_nr=%u ret=%ld pc=%08lx sp=%08lx\n",
            task_pid_nr(current),nr,value,regs->epc,regs->sp);
#endif
    if (!active || calls != returns+1) errors |= 2;
    if (returns < ARRAY_SIZE(trace)) {
        trace[returns].nr=nr;trace[returns].value=value;trace[returns].pc=pc;
    }
#ifdef CONFIG_ESP32C5_SHELL_CHECKPOINT
    if (nr==95 && value==0 && !(wait_options & WNOWAIT) && reap_samples<16) {
        int child;
        if (!get_user(child,&((siginfo_t __user *)wait_info)->si_pid) && child>0) {
            ++reap_samples;
            c5_trace("C5 REAP n=%u pid=%d free=%lu\n",reap_samples,child,nr_free_pages());
        }
    }
#endif
    ++returns; active=false;
    if (value == -ENOSYS) ++enosys;
    if (nr == 63 && value > 0) seen |= 64;
    if (nr == 172 && value == 1) seen |= 1;
    if (nr == 56 && value == -ENOENT) seen |= 2;
    if (nr == 403 && !value) seen |= 4;
    if ((nr == 64 || nr == 66) && arg0 == 1 && value > 0) seen |= 8;
    if ((nr == 214 || nr == 222) && value > 0) seen |= 16;
    if (nr == 29 && arg1 == 0x5409 && !value) seen |= 32;
}
void __noreturn c5_libc_exit(long code, const struct c5_clock_stats *clock,
    unsigned int restored)
{
    unsigned int rx,tx,polls,result;
    c5_tty_stats(&rx,&tx,&polls);
#ifdef CONFIG_ESP32C5_SHELL_CHECKPOINT
    result=code || !entered || errors || (seen & 0x49) != 0x49 || !active ||
        task_pid_nr(current) != 1 || (nr != 94 && nr != 93) ||
        calls < 10 || calls > TRACE_MAX || returns+1 != calls || !restored ||
        !rx || !tx || !polls || clock->irqs < 2 || clock->irqs >= C5_SHELL_TICK_LIMIT ||
        clock->callbacks != clock->irqs || clock->acked != clock->irqs ||
        clock->jiffies_delta != clock->irqs || clock->faults ||
        !clock->source_off || !irqs_disabled() || (csr_read(0xfb1)&0xff000000UL);
    pr_notice("C5 SHELL v1 result=%08x pid=%08x entered=%08x calls=%08x returns=%08x seen=%08x errors=%08x exit_code=%08lx enosys=%08x rx_bytes=%08x tx_bytes=%08x polls=%08x protection_restored=%08x irq_count=%08x callbacks=%08x acked=%08x jiffies_delta=%08lx clock_faults=%08x source_off=%08x irq_off=%08x level_after=%08lx\n",
        result,task_pid_nr(current),entered,calls,returns,seen,errors,code,enosys,
        rx,tx,polls,restored,clock->irqs,clock->callbacks,clock->acked,clock->jiffies_delta,
        clock->faults,clock->source_off,irqs_disabled(),csr_read(0xfb1)&0xff000000UL);
    if (result) pr_emerg("C5 SHELL FAIL: shell/syscall/context/clock checks\n");
    else pr_notice("C5 SHELL CHECKPOINT: BusyBox hush PID1 returned; intentional halt at diagnostic exit\n");
    for (;;) cpu_relax();
#endif
    result=code || !entered || errors || seen != 63 || !active ||
        task_pid_nr(current) != 1 || (nr != 94 && nr != 93) ||
        calls < 7 || calls > TRACE_MAX || returns+1 != calls || !restored ||
        !tx || !polls || clock->irqs < 2 || clock->irqs >= 4096 ||
        clock->callbacks != clock->irqs || clock->acked != clock->irqs ||
        clock->jiffies_delta != clock->irqs || clock->faults ||
        !clock->source_off || !irqs_disabled() || (csr_read(0xfb1)&0xff000000UL);
    /* Application drained tty output before exit. Deferred trace avoids
     * printk interleaving with an incompletely transmitted userspace line. */
    for (unsigned int i=0;i<returns && i<ARRAY_SIZE(trace);++i)
        pr_notice("C5 LIBC CALL n=%08x nr=%08x ret=%08lx pc=%08lx\n",
                  i+1,trace[i].nr,trace[i].value,trace[i].pc);
    pr_notice("C5 LIBC v1 result=%08x pid=%08x entered=%08x calls=%08x returns=%08x seen=%08x errors=%08x exit_code=%08lx enosys=%08x tx_bytes=%08x polls=%08x protection_restored=%08x irq_count=%08x callbacks=%08x acked=%08x jiffies_delta=%08lx clock_faults=%08x source_off=%08x irq_off=%08x level_after=%08lx\n",
        result,task_pid_nr(current),entered,calls,returns,seen,errors,code,enosys,
        tx,polls,restored,clock->irqs,clock->callbacks,clock->acked,clock->jiffies_delta,
        clock->faults,clock->source_off,irqs_disabled(),csr_read(0xfb1)&0xff000000UL);
    if (result) pr_emerg("C5 LIBC FAIL: application/syscall/context/clock checks\n");
    else pr_notice("C5 LIBC CHECKPOINT: C PID1 libc and relocated pointers returned; intentional halt at diagnostic exit\n");
    for (;;) cpu_relax();
}
