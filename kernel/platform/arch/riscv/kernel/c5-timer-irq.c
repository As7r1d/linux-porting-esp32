// SPDX-License-Identifier: GPL-2.0
/* Bounded Linux IRQ test. Not a clocksource/clockevent or scheduler tick. */
#include <linux/init.h>
#include <linux/interrupt.h>
#include <linux/irq.h>
#include <linux/io.h>
#include <linux/of.h>
#include <linux/of_irq.h>
#include <linux/irqflags.h>
#include <linux/ptrace.h>
#include <asm/csr.h>
#include <asm/c5-bootstrap.h>

#define R(a) ((void __iomem *)(unsigned long)(a))
#define CONF R(0x6000a000)
#define ENA R(0x6000a064)
#define RAW R(0x6000a068)
#define ALARM BIT(22)
#define LINE R(0x2080105c)
#define ROUTE R(0x600100fc)
#define LIMIT 16U
static unsigned int count, acked, armed, fresh, error;
static u32 progress, last_progress, raw_seen, last_cause, last_pc;
static u32 min_lead;

static void io_fence(void) { asm volatile("fence iorw,iorw" ::: "memory"); }
static bool sample(u64 *value)
{
    writel(BIT(29) | BIT(30), R(0x6000a004));
    io_fence();
    for (unsigned int i = 0; i < 1024; ++i)
        if (readl(R(0x6000a004)) & BIT(29)) {
            *value = ((u64)(readl(R(0x6000a040)) & 0xfffff) << 32) |
                      readl(R(0x6000a044));
            return true;
        }
    return false;
}
static bool valid_deadline(u64 start, u64 now, u64 target)
{
    return target <= 0xfffffffffffffULL && now >= start && now < target &&
           target - now > 1024 && target - now <= 65536;
}
static bool arm(void)
{
    u64 start, now, target;
    if (!sample(&start)) return false;
    target = start + 65536;
    if (target > 0xfffffffffffffULL) return false;
    writel(target >> 32, R(0x6000a02c)); writel((u32)target, R(0x6000a030));
    writel(1, R(0x6000a058)); io_fence();
    if (!sample(&now) || !valid_deadline(start, now, target) ||
        readl(R(0x6000a02c)) != target >> 32 ||
        readl(R(0x6000a030)) != (u32)target || (readl(RAW) & 4)) return false;
    if (!min_lead || target - now < min_lead) min_lead = target - now;
    writel(4, ENA); writel(readl(CONF) | ALARM, CONF); io_fence();
    ++armed;
    return true;
}
static irqreturn_t c5_alarm_irq(int irq, void *cookie)
{
    struct pt_regs *regs = get_irq_regs();
    u32 raw = readl(RAW), observed = READ_ONCE(progress);
    writel(readl(ENA) & ~4U, ENA); writel(readl(CONF) & ~ALARM, CONF);
    if (raw & 4) writel(4, R(0x6000a06c));
    io_fence();
    for (unsigned int i = 0; i < 256 && (readl(RAW) & 4); ++i) io_fence();
    if (!(raw & 4) || (readl(RAW) & 4) || !regs ||
        (regs->cause & 0x80000fffUL) != 0x80000017UL ||
        (regs->status & 0x1800) != 0x1800 || !irqs_disabled() ||
        observed <= last_progress || READ_ONCE(count) >= LIMIT) {
        WRITE_ONCE(error, 1);
        return IRQ_HANDLED;
    }
    raw_seen |= raw & 4; last_cause = regs->cause; last_pc = regs->epc;
    last_progress = observed; ++fresh; ++acked;
    WRITE_ONCE(count, READ_ONCE(count) + 1);
    if (READ_ONCE(count) < LIMIT && !arm()) WRITE_ONCE(error, 2);
    return IRQ_HANDLED;
}

void __init __noreturn c5_irq_checkpoint(void)
{
    struct device_node *node;
    unsigned int irq = 0, result = 0;
    u32 conf0, ena0, tc0, hi0, lo0, route0, attr0, ctl0, cleanup = 0;
    unsigned long threshold0, tvec0;
    extern void handle_exception(void);

    pr_notice("C5 IRQ STAGE: Linux IRQ domain initialized; preparing SYSTIMER test\n");
    conf0 = readl(CONF); ena0 = readl(ENA); tc0 = readl(R(0x6000a03c));
    hi0 = readl(R(0x6000a02c)); lo0 = readl(R(0x6000a030));
    route0 = readl(ROUTE); attr0 = readb(LINE + 2); ctl0 = readb(LINE + 3);
    threshold0 = csr_read(0x347); tvec0 = csr_read(CSR_TVEC);
    if (!irqs_disabled() || !(conf0 & BIT(30)) || (conf0 & ALARM) || ena0 ||
        (readl(RAW) & 4) || (readl(LINE) & 0x101) || csr_read(CSR_SCRATCH) ||
        (csr_read(0xfb1) & 0xff000000UL)) result = 1;
    for (unsigned int i = 0; i < 84; ++i)
        if (i != 63 && (readl(R(0x60010000 + 4 * i)) & 63) == 23) result |= 2;
    node = of_find_compatible_node(NULL, NULL, "experimental,esp32c5-systimer-test");
    if (!node) result |= 4;
    if (!result) irq = irq_of_parse_and_map(node, 0);
    of_node_put(node);
    if (!irq) result |= 8;
    if (result) goto finish;

    writel((route0 & ~63U) | 23U, ROUTE);
    writeb(0xc0, LINE + 2); writeb(0xe0, LINE + 3);
    writel(0, R(0x6000a03c)); writel(1, R(0x6000a058));
    if ((readl(ROUTE) & 63) != 23 || readb(LINE + 2) != 0xc0 ||
        (readb(LINE + 3) & 0xe0) != 0xe0 || readl(R(0x6000a03c))) {
        result = 16; goto restore;
    }
    if (request_irq(irq, c5_alarm_irq, IRQF_NO_THREAD | IRQF_NO_SUSPEND,
                    "c5-systimer-checkpoint", &count)) {
        result = 32; goto restore;
    }
    csr_write(0x347, 0x1f);
    csr_write(CSR_TVEC, (unsigned long)handle_exception | 3UL);
    if (csr_read(CSR_TVEC) != ((unsigned long)handle_exception | 3UL) ||
        readb(LINE + 1) != 1) { result = 64; goto restore; }
    pr_notice("C5 IRQ STAGE: enabling 16 one-shot Linux timer IRQs\n");
    WRITE_ONCE(progress, 1); last_progress = 1;
    if (!arm()) { result = 128; goto restore; }
    local_irq_enable();
    for (unsigned int i = 0; i < 100000000 && READ_ONCE(count) < LIMIT &&
         !READ_ONCE(error); ++i) {
        WRITE_ONCE(progress, READ_ONCE(progress) + 1);
        cpu_relax();
    }
    local_irq_disable();
    if (READ_ONCE(count) != LIMIT || error || fresh != LIMIT || acked != LIMIT ||
        armed != LIMIT || min_lead <= 1024 || min_lead > 65536) result |= 256;
restore:
    local_irq_disable();
    writeb(0, LINE + 1); writel(ena0, ENA); writel(conf0 & ~ALARM, CONF);
    io_fence();
    if (readl(RAW) & 4) writel(4, R(0x6000a06c));
    io_fence();
    writel(tc0, R(0x6000a03c)); writel(hi0, R(0x6000a02c));
    writel(lo0, R(0x6000a030)); writel(1, R(0x6000a058));
    writel(route0, ROUTE); writeb(attr0, LINE + 2); writeb(ctl0, LINE + 3);
    csr_write(0x347, threshold0); csr_write(CSR_TVEC, tvec0);
    cleanup = readl(CONF) == conf0 && readl(ENA) == ena0 && !(readl(RAW) & 4) &&
        !readb(LINE + 1) && readl(ROUTE) == route0 &&
        readb(LINE + 2) == attr0 && readb(LINE + 3) == ctl0 &&
        readl(R(0x6000a03c)) == tc0 && readl(R(0x6000a02c)) == hi0 &&
        readl(R(0x6000a030)) == lo0 && csr_read(0x347) == threshold0 &&
        csr_read(CSR_TVEC) == tvec0;
    if (!cleanup || (csr_read(0xfb1) & 0xff000000UL)) result |= 512;
finish:
    pr_notice("C5 IRQ v1 result=%08x irq_count=%08x armed=%08x acked=%08x fresh=%08x error=%08x raw_seen=%08x cause=%08x pc=%08x min_lead=%08x cleanup=%08x irq_off=%08x level_after=%08lx\n",
              result, count, armed, acked, fresh, error, raw_seen, last_cause,
              last_pc, min_lead, cleanup, irqs_disabled(), csr_read(0xfb1) & 0xff000000UL);
    if (result) pr_emerg("C5 IRQ FAIL: bounded timer delivery/context/cleanup\n");
    else pr_notice("C5 IRQ CHECKPOINT: 16 Linux timer IRQs returned; intentional halt before clockevent/timekeeping/tasks\n");
    for (;;) cpu_relax();
}
