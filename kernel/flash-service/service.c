/* SPDX-License-Identifier: GPL-2.0 */
/* All code, literals, stack and command data stay in SRAM while cache is off.
 * ROM addresses/ABI: pinned ESP-IDF v5.5.1 ESP32-C5 ECO2.
 */
#include "abi.h"
typedef unsigned int u32;
#define REG(a) (*(volatile u32 *)(a))
#define SPI 0x60003000u
typedef struct {
    unsigned short command, command_bits;
    u32 *address, address_bits, *tx, tx_bits, *rx, rx_bits, dummy_bits;
} command;
_Static_assert(sizeof(command)==32, "ROM SPI RV32 ABI");
#define ROM_MODE ((void (*)(int,int))0x4000011cu)
#define ROM_CONFIG ((void (*)(int,command *))0x40000114u)
#define WRITEBACK ((void (*)(void))0x40000668u)
#define SUSPEND ((u32 (*)(void))0x400006b4u)
#define RESUME ((void (*)(u32))0x400006b8u)

static u32 cycles(void) { u32 v; __asm__ volatile("csrr %0,mcycle":"=r"(v)); return v; }

/* If the controller itself never becomes idle, PSRAM access cannot safely resume.
 * Emit a bounded USB diagnostic from SRAM and fail-stop instead of returning.
 */
static __attribute__((noreturn)) void stuck(void)
{
    const char *p="\r\nC5 FLASH SRAM FATAL: SPI command timeout; cache suspended\r\n";
    while (*p) {
        u32 budget=100000;
        while (!(REG(0x6000f004u)&2u) && --budget) { }
        if (!budget) break;
        REG(0x6000f000u)=(unsigned char)*p++;
        REG(0x6000f004u)=1u;
    }
    for (;;) __asm__ volatile("wfi");
}

static void transaction(unsigned int op, u32 offset, u32 *words, u32 length, int addressed, int writing)
{
    command c={.command=op,.command_bits=8,.address=&offset,
               .address_bits=addressed?24:0,.tx=writing&&length?words:0,
               .tx_bits=writing?length*8:0,.rx=!writing&&length?words:0,
               .rx_bits=writing?0:length*8};
    ROM_MODE(1,5);
    REG(SPI+8)&=~(1u<<8);
    REG(SPI+0x14)=0x000f070fu;
    REG(SPI+0x34)=(REG(SPI+0x34)&~3u)|2u; /* CS0 only; never address PSRAM CS1. */
    ROM_CONFIG(1,&c);
    REG(SPI+0x34)=(REG(SPI+0x34)&~3u)|2u;
    __asm__ volatile("fence iorw,iorw":::"memory");
    REG(SPI)=(1u<<18)|((op==2 || op==0x20)?(1u<<17):0);
    u32 start=cycles();
    while (REG(SPI)&(1u<<18)) if ((u32)(cycles()-start)>2400000u) stuck();
    if (!writing) for (u32 i=0;i<(length+3)/4;++i) words[i]=REG(SPI+0x58+4*i);
}

void c5_service(void)
{
    struct c5_request *r=(void *)C5_REQUEST_BASE;
    r->result=-22;
    if (!c5_request_valid(r)) return;
    /* Save only registers the ROM configuration/our command path changes. */
    u32 saved[13];
    static const u32 regs[]={4,8,0xc,0x10,0x14,0x18,0x1c,0x20,0x24,0x28,0x2c,0x34,0x3c};
    u32 mhcr, prediction_mask=0x1030u;
    __asm__ volatile("csrrc %0,0x7c1,%1":"=r"(mhcr):"r"(prediction_mask):"memory");
    WRITEBACK();
    u32 autoload=SUSPEND();
    for (u32 i=0;i<13;++i) saved[i]=REG(SPI+regs[i]);
    u32 id=0, status=0;
    /* While WIP is set the NOR may ignore JEDEC-ID commands. Status polling
     * must use 05h directly; identity is checked at probe and before mutations.
     */
    if (r->op==C5_OP_STATUS) {
        transaction(5,0,&status,1,0,0);
        r->data[0]=status&255;r->result=0;goto done;
    }
    transaction(0x9f,0,&id,3,0,0);
    if ((id&0xffffff)!=C5_FLASH_ID) { r->result=-19; goto done; }
    if (r->op==C5_OP_ID) { r->data[0]=id&0xffffff; r->result=0; goto done; }
    transaction(5,0,&status,1,0,0);
    if (status&1) { r->result=-16; goto done; }
    if (r->op==C5_OP_READ) {
        transaction(3,r->offset,r->data,r->length,1,0);
        r->result=0;
        goto done;
    }
    /* Never clear block protection or touch status/configuration/eFuse commands. */
    transaction(6,0,&status,0,0,0);
    transaction(5,0,&status,1,0,0);
    if (!(status&2)) { r->result=-13; goto done; }
    transaction(r->op==C5_OP_PROGRAM?2:0x20,r->offset,r->data,
                r->op==C5_OP_PROGRAM?r->length:0,1,1);
    r->result=0; /* Device WIP completion is polled by Linux with IRQs enabled. */
done:
    for (u32 i=0;i<13;++i) REG(SPI+regs[i])=saved[i];
    __asm__ volatile("fence iorw,iorw":::"memory");
    RESUME(autoload);
    __asm__ volatile("csrw 0x7c1,%0"::"r"(mhcr):"memory");
}
