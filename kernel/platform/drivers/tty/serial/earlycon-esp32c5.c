// SPDX-License-Identifier: GPL-2.0
/* Experimental ESP32-C5 USB Serial/JTAG boot console; no IRQ or RX support. */
#include <linux/console.h>
#include <linux/init.h>
#include <linux/io.h>
#include <linux/serial_core.h>

#define C5_EP1 ((void __iomem *)0x6000f000UL)
#define C5_EP1_CONF ((void __iomem *)0x6000f004UL)

static void c5_usb_putc(struct uart_port *port, unsigned char ch)
{
	unsigned int budget;

	/* Bounded polling: loss of the USB host must not hang the kernel. */
	for (budget = 0; budget < 1000000; budget++) {
		if (readl(C5_EP1_CONF) & 2) {
			writel(ch, C5_EP1);
			writel(1, C5_EP1_CONF);
			return;
		}
		cpu_relax();
	}
}

static void c5_usb_write(struct console *con, const char *s, unsigned int n)
{
	struct earlycon_device *dev = con->data;

	uart_console_write(&dev->port, s, n, c5_usb_putc);
}

static int __init c5_usb_setup(struct earlycon_device *dev, const char *opt)
{
	dev->con->write = c5_usb_write;
	return 0;
}
EARLYCON_DECLARE(esp32c5usb, c5_usb_setup);
