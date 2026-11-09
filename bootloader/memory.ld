OUTPUT_ARCH(riscv)
ENTRY(_start)

MEMORY
{
    code (rx) : ORIGIN = 0x40800000, LENGTH = 16K
    data (rw) : ORIGIN = 0x40804000, LENGTH = 8K
    stack (rw) : ORIGIN = 0x40806000, LENGTH = 8K
}

PHDRS
{
    text PT_LOAD FLAGS(5);
    data PT_LOAD FLAGS(6);
}

SECTIONS
{
    .text : {
        KEEP(*(.text.entry))
        *(.text .text.*)
        *(.rodata .rodata.* .srodata .srodata.*)
        . = ALIGN(4);
    } > code :text
    .data : {
        __global_pointer$ = . + 0x800;
        *(.data .data.* .sdata .sdata.*)
        . = ALIGN(4);
    } > data :data
    .bss (NOLOAD) : {
        __bss_start = .;
        *(.bss .bss.* .sbss .sbss.* COMMON)
        . = ALIGN(4);
        __bss_end = .;
    } > data :data
    __stack_bottom = ORIGIN(stack);
    __stack_top = ORIGIN(stack) + LENGTH(stack);
    /DISCARD/ : { *(.eh_frame .eh_frame.* .comment) }
}

ASSERT(__bss_end <= ORIGIN(stack), "Data/BSS overlaps diagnostic stack")
