/* SPDX-License-Identifier: GPL-2.0 */
#ifndef _ASM_RISCV_C5_BOOTSTRAP_H
#define _ASM_RISCV_C5_BOOTSTRAP_H
#include <linux/init.h>
#include <linux/printk.h>
extern bool c5_trace_enabled;
#define c5_trace(fmt, ...) do { \
    if (c5_trace_enabled) pr_notice(fmt, ##__VA_ARGS__); \
    else printk(KERN_DEBUG fmt, ##__VA_ARGS__); \
} while (0)
#ifdef CONFIG_ESP32C5_SHELL_CHECKPOINT
#define C5_USER_STACK_BYTES 32768UL
#define C5_SHELL_TICK_LIMIT 32768U
#else
#define C5_USER_STACK_BYTES 16384UL
#endif
void __init c5_memory_checkpoint(void);
void __init __noreturn c5_irq_checkpoint(void);
void __init c5_clock_prepare(void);
void __init __noreturn c5_tick_checkpoint(void);
struct c5_clock_stats {
    unsigned int irqs, callbacks, acked, faults, late, source_off;
    unsigned long jiffies_delta;
};
void __init c5_sched_clock_start(void);
void c5_sched_clock_stop(struct c5_clock_stats *stats);
void __init __noreturn c5_sched_checkpoint(void);
struct pt_regs;
void c5_user_prepare(struct pt_regs *regs);
void c5_user_sys_enter(struct pt_regs *regs);
void c5_user_sys_return(struct pt_regs *regs);
void __noreturn c5_user_exit(long code);
void c5_tty_stats(unsigned int *rx, unsigned int *tx, unsigned int *poll_count);
void c5_libc_sys_enter(struct pt_regs *, unsigned long, unsigned long, unsigned long, unsigned int);
void c5_libc_sys_return(struct pt_regs *);
void __noreturn c5_libc_exit(long code, const struct c5_clock_stats *, unsigned int restored);
#endif
