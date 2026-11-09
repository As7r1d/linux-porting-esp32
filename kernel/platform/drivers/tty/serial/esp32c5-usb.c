// SPDX-License-Identifier: GPL-2.0
/* Experimental fixed USB Serial/JTAG port. Timer polling, no USB IRQ routing. */
#include <linux/console.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/of.h>
#include <linux/serial_core.h>
#include <linux/serial.h>
#include <linux/timer.h>
#include <linux/tty_flip.h>
#include <linux/io.h>
#include <asm/c5-bootstrap.h>

static struct uart_port c5_port;
static struct timer_list c5_poll_timer;
static bool running, rx_enabled, tx_enabled;
static unsigned int polls;
static struct console c5_console;
static struct uart_driver c5_uart = {
    .owner = THIS_MODULE, .driver_name = "esp32c5-usb", .dev_name = "ttyCUSB",
    .nr = 1, .cons = &c5_console,
};
static bool tx_ready(struct uart_port *p) { return readl(p->membase+4) & 2; }
static void c5_stop_tx(struct uart_port *p) { tx_enabled = false; }
static void c5_tx(struct uart_port *p)
{
    unsigned char ch;
    unsigned int before = p->icount.tx;
    /* <64 avoids the USB full-packet/ZLP corner case. Preserve queued data. */
    uart_port_tx_limited(p, ch, 32, tx_ready(p), writel(ch,p->membase), ({}));
    if (p->icount.tx != before) writel(1,p->membase+4);
}
static void c5_start_tx(struct uart_port *p) { tx_enabled = true; c5_tx(p); }
static void c5_stop_rx(struct uart_port *p) { rx_enabled = false; }
static void c5_poll(struct timer_list *timer)
{
    struct uart_port *p = &c5_port;
    unsigned long flags;
    bool push = false;
    uart_port_lock_irqsave(p, &flags);
    if (running) {
        ++polls;
        if (rx_enabled) {
            for (unsigned int i=0; i<64 && (readl(p->membase+4)&4); ++i) {
                unsigned char ch = readl(p->membase);
                ++p->icount.rx;
                /* No SysRq: every received byte follows the tty input path. */
                uart_insert_char(p, 0, 0, ch, TTY_NORMAL);
                push = true;
            }
        }
        if (tx_enabled) c5_tx(p);
        mod_timer(&c5_poll_timer, jiffies+1);
    }
    uart_port_unlock_irqrestore(p, flags);
    if (push) tty_flip_buffer_push(&p->state->port);
}
static unsigned int c5_tx_empty(struct uart_port *p)
{
    /* Hardware exposes writable, not an independent host-delivery ACK. */
    return tx_ready(p) ? TIOCSER_TEMT : 0;
}
static unsigned int c5_get_mctrl(struct uart_port *p)
{ return TIOCM_CTS | TIOCM_DSR | TIOCM_CAR; }
static void c5_set_mctrl(struct uart_port *p, unsigned int value) { }
static int c5_startup(struct uart_port *p)
{
    unsigned long flags;
    uart_port_lock_irqsave(p, &flags);
    running = rx_enabled = true;
    mod_timer(&c5_poll_timer, jiffies+1);
    uart_port_unlock_irqrestore(p, flags);
    return 0;
}
static void c5_shutdown(struct uart_port *p)
{
    unsigned long flags;
    uart_port_lock_irqsave(p, &flags);
    running = rx_enabled = tx_enabled = false;
    uart_port_unlock_irqrestore(p, flags);
    del_timer_sync(&c5_poll_timer);
}
static void c5_termios(struct uart_port *p, struct ktermios *new, const struct ktermios *old)
{
    unsigned long flags;
    uart_port_lock_irqsave(p, &flags);
    /* USB link has no UART baud/framing registers. Keep tty timing metadata. */
    uart_update_timeout(p, new->c_cflag, 115200);
    uart_port_unlock_irqrestore(p, flags);
    tty_termios_encode_baud_rate(new,115200,115200);
}
static const char *c5_type(struct uart_port *p) { return "C5 USB Serial/JTAG"; }
static void c5_config(struct uart_port *p, int flags) { p->type = 1; }
static int c5_verify(struct uart_port *p, struct serial_struct *s) { return -EINVAL; }
static const struct uart_ops c5_ops = {
    .tx_empty=c5_tx_empty, .set_mctrl=c5_set_mctrl, .get_mctrl=c5_get_mctrl,
    .stop_tx=c5_stop_tx, .start_tx=c5_start_tx, .stop_rx=c5_stop_rx,
    .startup=c5_startup, .shutdown=c5_shutdown, .set_termios=c5_termios,
    .type=c5_type, .config_port=c5_config, .verify_port=c5_verify,
};
static void c5_putchar(struct uart_port *p, unsigned char ch)
{
    for (unsigned int i=0; i<1000000; ++i) {
        if (tx_ready(p)) { writel(ch,p->membase); writel(1,p->membase+4); return; }
        cpu_relax();
    }
}
static void c5_console_write(struct console *con, const char *s, unsigned int n)
{
    unsigned long flags;
    uart_port_lock_irqsave(&c5_port, &flags);
    uart_console_write(&c5_port,s,n,c5_putchar);
    uart_port_unlock_irqrestore(&c5_port,flags);
}
static int c5_console_setup(struct console *con, char *options)
{
    if (con->index > 0 || !c5_port.membase) return -ENODEV;
    con->index=0;
    return uart_set_options(&c5_port,con,115200,'n',8,'n');
}
static struct console c5_console = {
    .name="ttyCUSB", .write=c5_console_write, .device=uart_console_device,
    .setup=c5_console_setup, .flags=CON_PRINTBUFFER, .index=-1, .data=&c5_uart,
};
void c5_tty_stats(unsigned int *rx, unsigned int *tx, unsigned int *poll_count)
{
    *rx=c5_port.icount.rx; *tx=c5_port.icount.tx; *poll_count=polls;
}
/* USB host modem-control changes must not restart an active PSRAM kernel.
 * C5 USB_SERIAL_JTAG_CHIP_RST +0x4c bit2 is volatile; bits0/1 are status.
 * Manual BOOT+RESET/power-cycle still provides the ROM recovery path.
 */
static u32 c5_usb_reset_control(u32 before)
{
    return (before & ~3u) | BIT(2);
}
static int c5_probe(struct platform_device *pdev)
{
    struct uart_port *p=&c5_port;
    p->membase=devm_platform_get_and_ioremap_resource(pdev,0,NULL);
    if (IS_ERR(p->membase)) return PTR_ERR(p->membase);
    {
        u32 before=readl(p->membase+0x4c),after;
        writel(c5_usb_reset_control(before),p->membase+0x4c);
        after=readl(p->membase+0x4c);
        if (!(after & BIT(2))) return -EIO;
        pr_notice("C5 USB RESET POLICY before=%08x after=%08x serial_reset_disabled=1\n",before,after);
    }
    p->dev=&pdev->dev; p->mapbase=0x6000f000; p->iotype=UPIO_MEM;
    p->flags=UPF_BOOT_AUTOCONF; p->ops=&c5_ops; p->fifosize=64; p->line=0;
    spin_lock_init(&p->lock); timer_setup(&c5_poll_timer,c5_poll,0);
    return uart_add_one_port(&c5_uart,p);
}
static const struct of_device_id c5_match[] = {
    { .compatible="experimental,esp32c5-usb-serial" }, {}
};
static struct platform_driver c5_driver = {
    .probe=c5_probe, .driver={ .name="esp32c5-usb", .of_match_table=c5_match },
};
static int __init c5_tty_init(void)
{
    int err=uart_register_driver(&c5_uart);
    if (err) return err;
    err=platform_driver_register(&c5_driver);
    if (err) uart_unregister_driver(&c5_uart);
    return err;
}
device_initcall(c5_tty_init);
MODULE_LICENSE("GPL");
