/* SPDX-License-Identifier: GPL-2.0 */
.section .text.entry,"ax"
.global _entry
_entry:
    addi sp,sp,-16
    sw ra,12(sp)
    sw s0,8(sp)
    mv s0,sp
    li sp,0x4080e000
    call c5_service
    mv sp,s0
    lw s0,8(sp)
    lw ra,12(sp)
    addi sp,sp,16
    ret
