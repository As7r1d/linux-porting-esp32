/* Standalone diagnostic, not Linux startup code. */
    .section .text.entry,"ax",@progbits
    .global _start
    .type _start,@function
_start:
    csrci mstatus, 8
    .option push
    .option norelax
    la sp, __stack_top
    la gp, __global_pointer$
    .option pop
    li tp, 0
    la t0, trap_entry
    ori t0, t0, 3             /* C5 supports CLIC mode only. */
    csrw mtvec, t0
    la t0, __bss_start
    la t1, __bss_end
1:
    bgeu t0, t1, 2f
    sw zero, 0(t0)
    addi t0, t0, 4
    j 1b
2:
    fence.i
    call diagnostic_main
3:
    j 3b
    .size _start, .-_start

    .balign 64
    .type trap_entry,@function
trap_entry:
    /* Fail-stop only: reset stack, report, never return to faulting code. */
    csrci mstatus, 8
    la sp, __stack_top
    csrr a0, mcause
    csrr a1, mepc
    csrr a2, mtval
    tail diagnostic_trap
    .size trap_entry, .-trap_entry
