// SPDX-License-Identifier: GPL-2.0
/* Experimental, UP/M-mode C5 CLIC. Level, non-SHV IRQs only. */
#include <linux/irq.h>
#include <linux/irqchip.h>
#include <linux/irqdomain.h>
#include <linux/io.h>
#include <linux/of.h>
#include <asm/irq.h>
#include <asm/ptrace.h>

#define C5_CLIC ((void __iomem *)0x20800000UL)
static struct irq_domain *c5_domain;

static void c5_mask(struct irq_data *d)
{
    writeb(0, C5_CLIC + 0x1001 + d->hwirq * 4);
}
static void c5_unmask(struct irq_data *d)
{
    writeb(1, C5_CLIC + 0x1001 + d->hwirq * 4);
}
static struct irq_chip c5_chip = {
    .name = "esp32c5-clic", .irq_mask = c5_mask, .irq_unmask = c5_unmask,
};
static int c5_map(struct irq_domain *d, unsigned int irq, irq_hw_number_t hw)
{
    if (hw < 16 || hw >= 48) return -EINVAL;
    irq_set_chip_and_handler(irq, &c5_chip, handle_level_irq);
    irq_set_status_flags(irq, IRQ_LEVEL);
    return 0;
}
static const struct irq_domain_ops c5_ops = {
    .map = c5_map, .xlate = irq_domain_xlate_onecell,
};
static void c5_dispatch(struct pt_regs *regs)
{
    unsigned int code = regs->cause & 0xfff;
    if (!(regs->cause & 0x80000000UL) || code < 16 || code >= 48 ||
        generic_handle_domain_irq(c5_domain, code))
        panic("C5 unexpected CLIC cause=%08lx pc=%08lx", regs->cause, regs->epc);
}
static int __init c5_init(struct device_node *node, struct device_node *parent)
{
    if (parent || (readl(C5_CLIC + 4) & 0x1fff) != 48 || readl(C5_CLIC) != 0x16)
        return -EINVAL;
    for (unsigned int i = 0; i < 48; ++i) {
        writeb(0, C5_CLIC + 0x1001 + 4 * i);
        if (readb(C5_CLIC + 0x1001 + 4 * i)) return -EIO;
    }
    c5_domain = irq_domain_add_linear(node, 48, &c5_ops, NULL);
    if (!c5_domain) return -ENOMEM;
    set_handle_irq(c5_dispatch);
    pr_notice("C5 CLIC: 48 sources; level/non-SHV domain ready\n");
    return 0;
}
IRQCHIP_DECLARE(esp32c5_clic, "experimental,esp32c5-clic", c5_init);
