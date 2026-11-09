// SPDX-License-Identifier: GPL-2.0
/* Bounded M-mode boot observation, before scheduler/IRQ/timer initialization. */
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/mm.h>
#include <linux/memblock.h>
#include <linux/slab.h>
#include <linux/vmstat.h>
#include <linux/of_fdt.h>
#include <linux/libfdt.h>
#include <linux/irqflags.h>
#include <asm/sections.h>
#include <asm/c5-bootstrap.h>

#define C5_RAM_START 0x42000000UL
#define C5_RAM_END 0x42800000UL
#define C5_DT_START 0x427f0000UL

static bool c5_allocation_valid(unsigned long addr, unsigned long bytes,
                                unsigned long kernel_end)
{
    return bytes && addr >= kernel_end && addr >= C5_RAM_START &&
           addr < C5_DT_START && bytes <= C5_DT_START - addr;
}

void __init c5_memory_checkpoint(void)
{
    struct memblock_region *region;
    unsigned long kernel_end = (unsigned long)_end;
    unsigned long page_addr = 0, total, free;
    unsigned int result = 0, page_checks = 0, slab_checks = 0;
    bool kernel_reserved = false, dt_nomap = false;
    u8 *small = NULL;

    pr_notice("C5 MEM STAGE: mm_core_init done\n");
    if (!irqs_disabled()) result |= 1;
    if (memblock_start_of_DRAM() != C5_RAM_START ||
        memblock_end_of_DRAM() != C5_RAM_END ||
        memblock_phys_mem_size() != C5_RAM_END - C5_RAM_START)
        result |= 2;
    for_each_mem_region(region) {
        if (region->base < C5_RAM_START || region->base + region->size > C5_RAM_END)
            result |= 4;
        if (region->base <= C5_DT_START &&
            region->base + region->size >= C5_RAM_END && memblock_is_nomap(region))
            dt_nomap = true;
    }
    for_each_reserved_mem_region(region)
        if (region->base <= C5_RAM_START && region->base + region->size >= kernel_end)
            kernel_reserved = true;
    if (!kernel_reserved || !dt_nomap || kernel_end >= C5_DT_START ||
        (unsigned long)_start != C5_RAM_START ||
        memblock_is_region_memory(0x40800000, 0x8000) ||
        !memblock_is_region_reserved(C5_DT_START, 0x10000))
        result |= 8;
    if ((unsigned long)initial_boot_params != C5_DT_START ||
        fdt_check_header(initial_boot_params) ||
        fdt_totalsize(initial_boot_params) > 0x10000)
        result |= 16;

    total = totalram_pages();
    if (!total || total > (C5_DT_START - C5_RAM_START) / PAGE_SIZE)
        result |= 32;
    if (!result) {
        page_addr = __get_free_page(GFP_NOWAIT | __GFP_NOWARN);
        if (!c5_allocation_valid(page_addr, PAGE_SIZE, kernel_end) ||
            !IS_ALIGNED(page_addr, PAGE_SIZE)) {
            result |= 64;
        } else {
            volatile u32 *words = (volatile u32 *)page_addr;
            for (unsigned int i = 0; i < PAGE_SIZE / sizeof(u32); ++i)
                words[i] = 0xc5a50000U ^ (u32)page_addr ^ i;
            for (unsigned int i = 0; i < PAGE_SIZE / sizeof(u32); ++i) {
                if (words[i] != (0xc5a50000U ^ (u32)page_addr ^ i)) result |= 128;
                ++page_checks;
            }
        }
        /* A bad allocator address is a stop condition; do not free an invalid pointer. */
        if (page_addr && !(result & 64)) free_page(page_addr);
    }
    if (!result) {
        small = kmalloc(64, GFP_NOWAIT | __GFP_NOWARN);
        if (!c5_allocation_valid((unsigned long)small, 64, kernel_end)) {
            result |= 256;
        } else {
            for (unsigned int i = 0; i < 64; ++i) small[i] = (u8)(i ^ 0xc5);
            barrier();
            for (unsigned int i = 0; i < 64; ++i) {
                if (READ_ONCE(small[i]) != (u8)(i ^ 0xc5)) result |= 512;
                ++slab_checks;
            }
            kfree(small);
        }
    }
    if (!irqs_disabled()) result |= 1024;
    free = global_zone_page_state(NR_FREE_PAGES);
    if (!free || free > total) result |= 2048;
    pr_notice("C5 MEM v1 result=%08x ram_start=42000000 ram_end=42800000 kernel_end=%08lx dt_start=427f0000 kernel_reserved=%08x dt_nomap=%08x total_pages=%08lx free_pages=%08lx page_addr=%08lx page_checks=%08x slab_checks=%08x irq_off=%08x\n",
              result, kernel_end, kernel_reserved, dt_nomap, total, free,
              page_addr, page_checks, slab_checks, irqs_disabled());
    if (result)
        pr_emerg("C5 MEM FAIL: memory checks failed; intentional halt\n");
    else {
#ifdef CONFIG_ESP32C5_IRQ_CHECKPOINT
        pr_notice("C5 MEM READY: allocator checks passed; continuing to IRQ checkpoint\n");
        return;
#else
        pr_notice("C5 MEMORY CHECKPOINT: allocator checks passed; intentional halt before scheduler/IRQ/timer init\n");
#endif
    }
    for (;;) cpu_relax();
}
