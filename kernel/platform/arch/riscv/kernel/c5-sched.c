// SPDX-License-Identifier: GPL-2.0
/* Voluntary kernel-thread switching checkpoint, before kernel_init_freeable. */
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/kthread.h>
#include <linux/pid.h>
#include <linux/completion.h>
#include <linux/sched.h>
#include <linux/atomic.h>
#include <linux/jiffies.h>
#include <linux/timekeeping.h>
#include <linux/irqflags.h>
#include <asm/csr.h>
#include <asm/c5-bootstrap.h>

#define ROUNDS 8U
struct worker_result { unsigned int id, rounds, checks, wakes; pid_t pid; unsigned long switches; };
static struct worker_result workers[2] = { { .id = 0 }, { .id = 1 } };
static struct completion turn[2];
static DECLARE_COMPLETION(done);
static atomic_t sequence = ATOMIC_INIT(0);
static atomic_t failed = ATOMIC_INIT(0);
static int worker(void *arg)
{
    struct worker_result *w = arg;
    volatile struct { u32 low, words[16], high; } frame;
    unsigned long switches = current->nvcsw;
    w->pid = task_pid_nr(current);
    frame.low = 0x13579bdf; frame.high = 0x2468ace0;
    if (irqs_disabled() || !(current->flags & PF_KTHREAD)) atomic_or(1, &failed);
    for (unsigned int round = 0; round < ROUNDS && !atomic_read(&failed); ++round) {
        unsigned long start;
        for (unsigned int i = 0; i < 16; ++i)
            frame.words[i] = 0xc5000000U ^ (w->id << 16) ^ (round << 8) ^ i;
        if (!wait_for_completion_timeout(&turn[w->id], 2 * HZ)) {
            atomic_or(2, &failed); break;
        }
        if (atomic_read(&failed)) break;
        if (atomic_cmpxchg(&sequence, 2 * round + w->id, 2 * round + w->id + 1) !=
            2 * round + w->id) { atomic_or(4, &failed); break; }
        start = jiffies;
        schedule_timeout_uninterruptible(w->id + 1);
        if (jiffies - start < w->id + 1 || jiffies - start > 2 * HZ)
            atomic_or(8, &failed);
        else ++w->wakes;
        if (frame.low != 0x13579bdf || frame.high != 0x2468ace0) atomic_or(16, &failed);
        w->checks += 2;
        for (unsigned int i = 0; i < 16; ++i) {
            if (frame.words[i] != (0xc5000000U ^ (w->id << 16) ^ (round << 8) ^ i))
                atomic_or(32, &failed);
            ++w->checks;
        }
        if (irqs_disabled()) atomic_or(64, &failed);
        ++w->rounds;
        complete(&turn[w->id ^ 1]);
    }
    w->switches = current->nvcsw - switches;
    complete(&done);
    return 0;
}
void __init __noreturn c5_sched_checkpoint(void)
{
    struct c5_clock_stats clock;
    unsigned int created = 0, completed = 0, result = 0;
    unsigned long j0 = jiffies, elapsed;
    u64 n0 = ktime_get_ns(), ns;
    pid_t coordinator = task_pid_nr(current);
    pr_notice("C5 SCHED STAGE: kernel init pid=%d; kthreadd ready; creating two workers\n", coordinator);
    if (coordinator != 1 || irqs_disabled() || in_interrupt()) result |= 1;
    init_completion(&turn[0]); init_completion(&turn[1]);
    if (!result) {
        for (unsigned int i = 0; i < 2; ++i) {
            struct task_struct *task = kthread_run(worker, &workers[i], "c5-sched-%u", i);
            if (IS_ERR(task)) { result |= 2; atomic_or(128, &failed); break; }
            ++created;
        }
    }
    if (created == 2) complete(&turn[0]);
    else { atomic_or(128, &failed); complete_all(&turn[0]); complete_all(&turn[1]); }
    for (unsigned int i = 0; i < created; ++i)
        if (wait_for_completion_timeout(&done, 5 * HZ)) ++completed;
        else { result |= 4; atomic_or(256, &failed); }
    elapsed = jiffies - j0; ns = ktime_get_ns() - n0;
    if (atomic_read(&failed) || completed != 2 || atomic_read(&sequence) != 16 ||
        workers[0].rounds != 8 || workers[1].rounds != 8 ||
        workers[0].pid < 3 || workers[1].pid < 3 || workers[0].pid == workers[1].pid ||
        workers[0].checks + workers[1].checks != 288 ||
        workers[0].wakes + workers[1].wakes != 16 ||
        workers[0].switches < 8 || workers[1].switches < 8 || elapsed < 24 ||
        elapsed > 10 * HZ || !ns || irqs_disabled()) result |= 8;
    c5_sched_clock_stop(&clock);
    if (clock.irqs < 24 || clock.irqs >= 4096 || clock.callbacks != clock.irqs ||
        clock.acked != clock.irqs || clock.jiffies_delta != clock.irqs || clock.faults ||
        !clock.source_off || !irqs_disabled() || (csr_read(0xfb1) & 0xff000000UL)) result |= 16;
    pr_notice("C5 SCHED v1 result=%08x coordinator=%08x created=%08x completed=%08x pid_a=%08x pid_b=%08x rounds_a=%08x rounds_b=%08x steps=%08x stack_checks=%08x wake_checks=%08x switches_a=%08lx switches_b=%08lx elapsed_ticks=%08lx ns_delta=%016llx worker_errors=%08x irq_count=%08x callbacks=%08x acked=%08x jiffies_delta=%08lx clock_faults=%08x late=%08x source_off=%08x irq_off=%08x level_after=%08lx\n",
              result, coordinator, created, completed, workers[0].pid, workers[1].pid,
              workers[0].rounds, workers[1].rounds, atomic_read(&sequence),
              workers[0].checks + workers[1].checks, workers[0].wakes + workers[1].wakes,
              workers[0].switches, workers[1].switches, elapsed, ns, atomic_read(&failed),
              clock.irqs, clock.callbacks, clock.acked, clock.jiffies_delta, clock.faults,
              clock.late, clock.source_off, irqs_disabled(), csr_read(0xfb1) & 0xff000000UL);
    if (result) pr_emerg("C5 SCHED FAIL: worker sleep/wake/context/clock checks\n");
    else pr_notice("C5 SCHED CHECKPOINT: two kernel threads slept, woke and switched; intentional halt before userspace\n");
    for (;;) cpu_relax();
}
