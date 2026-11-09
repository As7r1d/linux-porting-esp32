OUTPUT_ARCH(riscv)
ENTRY(_entry)
SECTIONS {
    . = 0x40808000;
    .text : { KEEP(*(.text.entry)) *(.text .text.*) *(.rodata .rodata.*) }
    .data : { *(.data .data.* .sdata .sdata.*) }
    .bss : { *(.bss .bss.* .sbss .sbss.* COMMON) }
    /DISCARD/ : { *(.eh_frame*) *(.comment) *(.riscv.attributes) }
}
ASSERT(SIZEOF(.text) <= 0x2000, "Service code overlaps SRAM request")
ASSERT(SIZEOF(.data) == 0 && SIZEOF(.bss) == 0, "Service must not depend on global initialization")
