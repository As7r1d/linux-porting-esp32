/* Flash-boot candidate: same PSRAM bootstrap as the qualified USB loader.
 * ROM loads this image into SRAM; explicit SPI reads avoid flash XIP.
 */
#include <stdint.h>
/* Exact measured download/normal-Flash profiles, same enabled NAPOT window.
 * Reject changed address, lock, cache policy or any other permission bits.
 */
static int flash_pma_valid(uint32_t cfg,uint32_t addr)
{
    return addr==0x10bfffffu && (cfg==0xc0000011u || cfg==0xc0000015u);
}
#define diagnostic_main usb_loader_unused
#include "build/psram-loader.inc"
#undef diagnostic_main

#define META_FLASH 0x10000u
#define KERNEL_FLASH 0x20000u
#define DT_FLASH 0x220000u
#define ROOTFS_FLASH 0x230000u
#define ROOTFS_OFF 0x700000u
#define ROOTFS_MAX 0x0f0000u

static int flash_header_valid(const uint32_t *h)
{
    uint32_t legacy[16];
    for (unsigned i=0;i<16;++i) legacy[i]=h[i];
    legacy[1]=1;
    for (unsigned i=6;i<16;++i) legacy[i]=0;
    if (!valid_header(legacy) || h[1]!=2 || h[6]<64 ||
        h[6]>ROOTFS_MAX || (h[6]&63)) return 0;
    for (unsigned i=8;i<16;++i) if(h[i]) return 0;
    return 1;
}

static void boot_watchdogs(void)
{
    /* Pinned C5 LP_WDT and TIMG definitions; RAM-only configuration writes. */
    REG32(0x600b1c18u)=0x50d83aa1u;
    REG32(0x600b1c00u)&=~((1u<<31)|(1u<<12));
    REG32(0x600b1c18u)=0;
    REG32(0x600b1c20u)=0x50d83aa1u;
    REG32(0x600b1c1cu)|=1u<<18; /* super-WDT auto-feed */
    REG32(0x600b1c20u)=0;
    for(uint32_t base=0x60008000u;base<=0x60009000u;base+=0x1000u) {
        REG32(base+0x64)=0x50d83aa1u;
        REG32(base+0x48)&=~((1u<<31)|(1u<<14));
        REG32(base+0x64)=0;
    }
    __asm__ volatile("fence iorw,iorw":::"memory");
}
static void flash_read64(uint32_t off, uint32_t *dest)
{
    if((off&63u) || off>0x800000u-64u)fatal(30);
    rom_spi_command c={.command=3,.command_bits=8,.address=&off,.address_bits=24,
        .rx=dest,.rx_bits=512};
    rom_spi_mode(1,5); SPI1_CTRL&=~(1u<<8);SPI1_CLOCK=test_spi_clock;
    SPI1_MISC=(SPI1_MISC|2u)&~1u;rom_spi_config(1,&c);
    SPI1_MISC=(SPI1_MISC|2u)&~1u;
    if((SPI1_MISC&3u)!=2u)fatal(31);
    __asm__ volatile("fence iorw,iorw":::"memory");
    rom_spi_start(1,(uint8_t *)dest,64,1,0); /* CS0 READ only */
}
static void from_flash(uint32_t src,uint32_t dest,uint32_t size,uint32_t expected)
{
    uint32_t hash=2166136261u;
    for(uint32_t pos=0;pos<size;pos+=64) {
        flash_read64(src+pos,packet+3);
        transfer(1,dest+pos,packet+3);transfer(0,dest+pos,verify);
        for(uint32_t j=0;j<16;++j)if(verify[j]!=packet[j+3])fatal(32);
        hash=fnv(hash,(uint8_t *)verify,64);
    }
    if(hash!=expected)fatal(33);
}
__attribute__((noreturn)) void diagnostic_main(void)
{
    boot_watchdogs();
    put_string("\r\nC5 FLASH BOOT v1\r\n");
    if(cookie!=0x43354c44u || read_psram_id())fatal(34);
    uint32_t packed=(extended_id[0]>>16)|(extended_id[1]<<16);
    uint32_t eid=__builtin_bswap32(packed);
    if(psram_ids[0]!=0x5a5d0du || flash_ids[0]!=0x174020u || flash_ids[1]!=0x174020u
        || nominal_size!=0x800000u || ((eid>>25)&1u)==0 || ((eid>>5)&0xfffffu)==0x8a445u)fatal(35);
    flash_read64(META_FLASH,header);
    if(!flash_header_valid(header))fatal(36);
    SPI0_SMEM_AC=configured_smem_ac;
    from_flash(KERNEL_FLASH,0,header[2],header[4]);
    from_flash(DT_FLASH,DT_OFF,header[3],header[5]);
    from_flash(ROOTFS_FLASH,ROOTFS_OFF,header[6],header[7]);
    /* Observe actual normal-boot PMA baseline before enforcing the original guard. */
    uint32_t pma_cfg,pma_addr;
    __asm__ volatile("csrr %0,0xbcf;csrr %1,0xbdf":"=r"(pma_cfg),"=r"(pma_addr));
    put_string("\r\nC5 FLASH PMA BEFORE cfg=");put_hex(pma_cfg);
    put_string(" addr=");put_hex(pma_addr);put_string("\r\n");
    map_psram();
    if(fnv(2166136261u,(const uint8_t *)BASE,header[2])!=header[4] ||
        fnv(2166136261u,(const uint8_t *)(BASE+DT_OFF),header[3])!=header[5] ||
        fnv(2166136261u,(const uint8_t *)(BASE+ROOTFS_OFF),header[6])!=header[7])fatal(37);
    if(REG32(BASE+ROOTFS_OFF)!=0x28cd3d45u)fatal(44);
    put_string("\r\nC5 FLASH ROOTFS VERIFIED: cramfs at 42700000\r\n");
    if(REG32(BASE+56)!=0x05435352u || REG32(BASE+DT_OFF)!=0xedfe0dd0u)fatal(38);
    uint32_t pmp;__asm__ volatile("csrr %0,pmpcfg0":"=r"(pmp));if(pmp)fatal(39);
    uint32_t lines=REG32(0x20800004u)&0x1fffu;if(!lines||lines>128)fatal(40);
    for(uint32_t i=0;i<lines;++i) {
        *(volatile uint8_t *)(0x20801001u+4*i)=0;
        if(*(volatile uint8_t *)(0x20801001u+4*i))fatal(41);
    }
    if(REG32(0x20800000u)!=6u)fatal(42);
    REG32(0x20800000u)=0x16u;
    if(REG32(0x20800000u)!=0x16u)fatal(43);
    put_string("\r\nC5 FLASH VERIFIED; JUMP entry=42000000 dt=427f0000\r\n");
    __asm__ volatile("csrci mstatus,8;fence iorw,iorw;fence.i;li a0,0;li a1,0x427f0000;li t0,0x42000000;jr t0":::"memory");
    __builtin_unreachable();
}
