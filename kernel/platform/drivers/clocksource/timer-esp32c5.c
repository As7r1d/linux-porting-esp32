// SPDX-License-Identifier: GPL-2.0
/* Experimental UP/M-mode, bounded 32-tick checkpoint. XTAL nominal16MHz only. */
#include <linux/clocksource.h>
#include <linux/clockchips.h>
#include <linux/interrupt.h>
#include <linux/irq.h>
#include <linux/io.h>
#include <linux/of.h>
#include <linux/of_irq.h>
#include <linux/sched_clock.h>
#include <linux/sched/clock.h>
#include <linux/jiffies.h>
#include <linux/timekeeping.h>
#include <linux/irqflags.h>
#include <asm/csr.h>
#include <asm/c5-bootstrap.h>

#define R(a) ((void __iomem *)(unsigned long)(a))
#define CONF R(0x6000a000)
#define ENA R(0x6000a064)
#define RAW R(0x6000a068)
#define LINE R(0x2080105c)
#define FREQ 16000000U
#ifdef CONFIG_ESP32C5_SHELL_CHECKPOINT
#define LIMIT C5_SHELL_TICK_LIMIT
#elif defined(CONFIG_ESP32C5_SCHED_CHECKPOINT)
#define LIMIT 4096U
#else
#define LIMIT 32U
#endif
#define PERIOD (FREQ / HZ)
static u64 last_cycle, deadline;
static u32 faults, irqs, callbacks, acked, late, freq_config, xtal;
static bool prepared, gated, periodic;
static struct clock_event_device c5_event;

static void fence_io(void) { asm volatile("fence iorw,iorw" ::: "memory"); }
u64 notrace c5_systimer_cycles(void)
{
    unsigned long flags;
    u64 value;
    local_irq_save(flags);
    writel(BIT(29) | BIT(30), R(0x6000a004)); fence_io();
    for (unsigned int i = 0; i < 1024; ++i) {
        if (readl(R(0x6000a004)) & BIT(29)) {
            value = ((u64)(readl(R(0x6000a040)) & 0xfffff) << 32) |
                    readl(R(0x6000a044));
            if (value < last_cycle) { faults |= 1; value = last_cycle; }
            last_cycle = value;
            local_irq_restore(flags);
            return value;
        }
    }
    faults |= 2;
    value = last_cycle;
    local_irq_restore(flags);
    return value;
}
static u64 c5_cs_read(struct clocksource *cs) { return c5_systimer_cycles(); }
static struct clocksource c5_cs = {
    .name = "c5-systimer", .rating = 300, .read = c5_cs_read,
    .mask = CLOCKSOURCE_MASK(52), .flags = CLOCK_SOURCE_IS_CONTINUOUS,
};
/* This bounded bootstrap stops before clocksource selection initcalls run. */
struct clocksource * __init clocksource_default_clock(void) { return &c5_cs; }

static void stop_alarm(void)
{
    writel(readl(ENA) & ~4U, ENA); writel(readl(CONF) & ~BIT(22), CONF); fence_io();
}
/* Reserve 1/4 tick for the remaining enable writes and cache-miss latency. */
#define ARM_MIN_LEAD (PERIOD / 4)
static unsigned int rearm_retries;
static int arm_error;
static u64 arm_target, arm_now;
static u32 arm_hi, arm_lo, arm_raw;
static int arm_check(u64 target, u64 now, u32 hi, u32 lo, u32 raw, u32 failed)
{
    if (target > 0xfffffffffffffULL) return -EOVERFLOW;
    if (failed || hi != target >> 32 || lo != (u32)target) return -EIO;
    if (raw & 4) return -EBUSY;
    if (now >= target || target - now <= ARM_MIN_LEAD) return -ETIME;
    return 0;
}
static int program(u64 target)
{
    u64 now;
    u32 hi, lo, raw;
    int error;
    if (target > 0xfffffffffffffULL) return -EOVERFLOW;
    writel(target >> 32, R(0x6000a02c)); writel((u32)target, R(0x6000a030));
    writel(1, R(0x6000a058)); fence_io();
    hi = readl(R(0x6000a02c)); lo = readl(R(0x6000a030)); raw = readl(RAW);
    now = c5_systimer_cycles();
    error = arm_check(target, now, hi, lo, raw, faults);
    if (error) {
        arm_error = error; arm_target = target; arm_now = now;
        arm_hi = hi; arm_lo = lo; arm_raw = raw;
        return error;
    }
    deadline = target;
    writel(readl(ENA) | 4, ENA); writel(readl(CONF) | BIT(22), CONF); fence_io();
    return 0;
}
static int rearm(u64 next)
{
    int error;
    for (unsigned int attempt = 0; attempt < 3; ++attempt) {
        u64 now = c5_systimer_cycles();
        if (next <= now + ARM_MIN_LEAD) { next = now + PERIOD; ++late; }
        error = program(next);
        if (!error) return 0;
        /* Only a missed/near deadline can retry. No raw clearing here. */
        if (error != -ETIME) return error;
        ++rearm_retries;
        next = c5_systimer_cycles() + PERIOD;
        ++late;
    }
    return -ETIME;
}
static int c5_shutdown(struct clock_event_device *dev)
{
    periodic = false; stop_alarm(); return 0;
}
static int c5_periodic(struct clock_event_device *dev)
{
    periodic = true;
    /* The checkpoint opens this gate immediately before enabling global MIE. */
    return gated ? program(c5_systimer_cycles() + PERIOD) : 0;
}
static irqreturn_t c5_tick_irq(int irq, void *cookie)
{
    u32 raw = readl(RAW);
    stop_alarm();
    if (raw & 4) writel(4, R(0x6000a06c));
    fence_io();
    for (unsigned int i = 0; i < 256 && (readl(RAW) & 4); ++i) fence_io();
    if (!(raw & 4) || (readl(RAW) & 4) || !periodic || !gated || irqs >= LIMIT) {
        faults |= 4;
#ifdef CONFIG_ESP32C5_SCHED_CHECKPOINT
        panic("C5 scheduler timer unexpected IRQ");
#endif
        return IRQ_HANDLED;
    }
    ++irqs; ++acked;
    c5_event.event_handler(&c5_event);
    ++callbacks;
    if (irqs < LIMIT) {
        if (rearm(deadline + PERIOD)) faults |= 8;
    }
#ifdef CONFIG_ESP32C5_SCHED_CHECKPOINT
    if (faults || irqs >= LIMIT) {
        stop_alarm();
        panic("C5 scheduler timer fault=%u irq_count=%u arm_error=%d target=%016llx now=%016llx hi=%08x lo=%08x raw=%08x retries=%u late=%u",
              faults, irqs, arm_error, arm_target, arm_now, arm_hi, arm_lo, arm_raw, rearm_retries, late);
    }
#endif
    return IRQ_HANDLED;
}
static bool valid_clock_config(u32 pcr, u32 crystal)
{
    return !(pcr & BIT(20)) && (pcr & BIT(22)) && (crystal == 40 || crystal == 48);
}
void __init c5_clock_prepare(void)
{
    u64 a, b;
    extern void handle_exception(void);
    freq_config = readl(R(0x60096070));
    xtal = (readl(R(0x60096110)) >> 24) & 127;
    if (!irqs_disabled() || !valid_clock_config(freq_config, xtal) || !(readl(CONF) & BIT(30)) ||
        (readl(CONF) & BIT(22)) || readl(ENA) || (readl(RAW) & 4))
        panic("C5 clock precondition: PCR=%08x XTAL=%u", freq_config, xtal);
    a = c5_systimer_cycles(); b = c5_systimer_cycles();
    if (faults || b <= a) panic("C5 counter not advancing");
    csr_write(0x347, 0x1f);
    csr_write(CSR_TVEC, (unsigned long)handle_exception | 3UL);
    if (clocksource_register_hz(&c5_cs, FREQ)) panic("C5 clocksource registration");
    sched_clock_register(c5_systimer_cycles, 52, FREQ);
    prepared = true;
    pr_notice("C5 CLOCK: XTAL=%uMHz PCR=%08x nominal=%uHz; external calibration untested\n",
              xtal, freq_config, FREQ);
}
static int __init c5_timer_init(struct device_node *node)
{
    u32 freq;
    unsigned int irq;
    if (!prepared || of_property_read_u32(node, "clock-frequency", &freq) || freq != FREQ)
        return -EINVAL;
    for (unsigned int i = 0; i < 84; ++i)
        if (i != 63 && (readl(R(0x60010000 + i * 4)) & 63) == 23) return -EBUSY;
    if ((readl(LINE) & 0x101) || readl(ENA) || (readl(RAW) & 4)) return -EBUSY;
    irq = irq_of_parse_and_map(node, 0);
    if (!irq) return -EINVAL;
    writel((readl(R(0x600100fc)) & ~63U) | 23, R(0x600100fc));
    writeb(0xc0, LINE + 2); writeb(0xe0, LINE + 3);
    writel(0, R(0x6000a03c)); writel(1, R(0x6000a058));
    if ((readl(R(0x600100fc)) & 63) != 23 || readb(LINE + 2) != 0xc0 ||
        (readb(LINE + 3) & 0xe0) != 0xe0 || readl(R(0x6000a03c))) return -EIO;
    if (request_irq(irq, c5_tick_irq, IRQF_TIMER | IRQF_NO_THREAD,
                    "c5-clockevent", &c5_event)) return -EBUSY;
    c5_event.name = "c5-systimer"; c5_event.rating = 300;
    c5_event.features = CLOCK_EVT_FEAT_PERIODIC;
    c5_event.set_state_shutdown = c5_shutdown;
    c5_event.set_state_periodic = c5_periodic;
    c5_event.cpumask = cpumask_of(0);
    c5_event.irq = irq;
    clockevents_config_and_register(&c5_event, FREQ, 2048, 0xffffffffUL);
    pr_notice("C5 CLOCKEVENT: periodic HZ=%u; hardware one-shot rearm; start gated\n", HZ);
    return 0;
}
TIMER_OF_DECLARE(esp32c5_timer, "experimental,esp32c5-systimer", c5_timer_init);

static bool valid_elapsed(u64 ns, u64 cycles)
{
    u64 nominal;
    if (!cycles || cycles > 32000000ULL) return false;
    nominal = div_u64(cycles * 1000, 16);
    return ns >= 160000000ULL && ns <= 1280000000ULL &&
           (ns > nominal ? ns - nominal : nominal - ns) <= 2000000ULL;
}
void __init __noreturn c5_tick_checkpoint(void)
{
    u64 c0, c1, n0, n1, pre0, pre1, ns, cycles;
    unsigned long j0, jd;
    u32 result = 0, off;
    if (!prepared || !periodic || !clockevent_state_periodic(&c5_event) ||
        !c5_event.event_handler || !irqs_disabled())
        panic("C5 tick precondition: prepared=%u periodic=%u state_periodic=%u handler=%u irq_off=%u",
              prepared, periodic, clockevent_state_periodic(&c5_event),
              !!c5_event.event_handler, irqs_disabled());
    pre0 = ktime_get_ns();
    for (unsigned int i = 0; i < 128; ++i) (void)c5_systimer_cycles();
    pre1 = ktime_get_ns();
    if (pre1 <= pre0) panic("C5 timekeeping did not advance with IRQs disabled");
    pr_notice("C5 TICK STAGE: enabling 32 real Linux clockevent callbacks\n");
    j0 = jiffies; c0 = c5_systimer_cycles(); n0 = ktime_get_ns();
    gated = true;
    if (program(c5_systimer_cycles() + PERIOD)) panic("C5 first tick arm");
    local_irq_enable();
    for (unsigned int i = 0; i < 100000000 && READ_ONCE(irqs) < LIMIT && !READ_ONCE(faults); ++i)
        cpu_relax();
    local_irq_disable();
    c1 = c5_systimer_cycles(); n1 = ktime_get_ns(); jd = jiffies - j0;
    gated = false; c5_shutdown(&c5_event); writeb(0, LINE + 1);
    if (readl(RAW) & 4) writel(4, R(0x6000a06c));
    fence_io();
    off = !(readl(ENA) & 4) && !(readl(CONF) & BIT(22)) &&
          !(readl(RAW) & 4) && !readb(LINE + 1);
    cycles = c1 - c0; ns = n1 - n0;
    if (irqs != LIMIT || callbacks != LIMIT || acked != LIMIT || jd != LIMIT ||
        faults || !off || !irqs_disabled() || c1 <= c0 || n1 <= n0 ||
        !valid_elapsed(ns, cycles) ||
        (csr_read(0xfb1) & 0xff000000UL)) result = 1;
    pr_notice("C5 TICK v1 result=%08x irq_count=%08x callbacks=%08x acked=%08x jiffies_delta=%08lx hz=%08x freq=%08x faults=%08x late=%08x pcr=%08x xtal=%08x ns_delta=%016llx cycles_delta=%016llx pre_ns_advance=%016llx source_off=%08x irq_off=%08x level_after=%08lx\n",
              result, irqs, callbacks, acked, jd, HZ, FREQ, faults, late, freq_config,
              xtal, ns, cycles, pre1-pre0, off, irqs_disabled(), csr_read(0xfb1) & 0xff000000UL);
    if (result) pr_emerg("C5 TICK FAIL: clocksource/clockevent/timekeeping/tick checks\n");
    else pr_notice("C5 TICK CHECKPOINT: clocksource time and 32 Linux ticks advanced; intentional halt before tasks/userspace\n");
    for (;;) cpu_relax();
}

#ifdef CONFIG_ESP32C5_SCHED_CHECKPOINT
static unsigned long sched_j0;
void __init c5_sched_clock_start(void)
{
    if (!prepared || !periodic || !clockevent_state_periodic(&c5_event) ||
        !c5_event.event_handler || !irqs_disabled() || gated)
        panic("C5 scheduler clock start precondition");
    pr_notice("C5 SCHED CLOCK: continuous HZ=%u; diagnostic IRQ cap=%u\n", HZ, LIMIT);
    sched_j0 = jiffies;
    gated = true;
    if (program(c5_systimer_cycles() + PERIOD)) panic("C5 scheduler first tick");
}
void c5_sched_clock_stop(struct c5_clock_stats *stats)
{
    local_irq_disable();
    gated = false; c5_shutdown(&c5_event); writeb(0, LINE + 1);
    if (readl(RAW) & 4) writel(4, R(0x6000a06c));
    fence_io();
    pr_notice("C5 REARM v1 retries=%08x late=%08x last_error=%d target=%016llx now=%016llx hi=%08x lo=%08x raw=%08x\n",
              rearm_retries, late, arm_error, arm_target, arm_now, arm_hi, arm_lo, arm_raw);
    stats->irqs = irqs; stats->callbacks = callbacks; stats->acked = acked;
    stats->faults = faults; stats->late = late;
    stats->jiffies_delta = jiffies - sched_j0;
    stats->source_off = !(readl(ENA) & 4) && !(readl(CONF) & BIT(22)) &&
                        !(readl(RAW) & 4) && !readb(LINE + 1);
}
#endif
