#include <stdint.h>
#include <stddef.h>
void *memset(void *dest, int value, size_t count)
{
    volatile unsigned char *p=dest;
    for(size_t i=0;i<count;++i)p[i]=(unsigned char)value;
    return dest;
}
#define USB_EP1 (*(volatile uint32_t *)0x6000f000u)
#define USB_EP1_CONF (*(volatile uint32_t *)0x6000f004u)
#define USB_TX_FREE 2u
#define USB_WR_DONE 1u
static volatile uint32_t cookie = 0x43354c44u;
static uint32_t psram_ids[3], extended_id[2], flash_ids[2];
static uint32_t completed_reads, stage, nominal_size, rom_eco, before_id;
static uint32_t saved_smem_ac, configured_smem_ac, saved_spi_clock, test_spi_clock;
static void snapshot(const char *label);
extern void rom_cache_boot_init(void);
extern uint32_t rom_cache_disable(void);
extern void rom_cache_enable(uint32_t autoload);
static void delay(void)
{
    /* Deliberately not calibrated time. Keep USB and watchdog setup inherited. */
    for (volatile uint32_t n = 0; n < 1000000u; ++n)
        __asm__ volatile ("nop");
}

static int put_char(char ch)
{
    for (uint32_t n = 0; n < 1000000u; ++n) {
        if (USB_EP1_CONF & USB_TX_FREE) {
            USB_EP1 = (uint32_t)(uint8_t)ch;
            USB_EP1_CONF = USB_WR_DONE;
            return 1;
        }
    }
    return 0;  /* No host reader: don't spin forever waiting for USB. */
}

static void put_string(const char *s)
{
    while (*s)
        if (!put_char(*s++))
            break;
}

static void put_hex(uint32_t value)
{
    const char *digits = "0123456789abcdef";
    for (int shift = 28; shift >= 0; shift -= 4)
        if (!put_char(digits[(value >> shift) & 15u]))
            break;
}

/* ABI layout checked against the pinned C5 ROM opi_flash.h. Lengths are bits. */
typedef struct {
    uint16_t command;
    uint16_t command_bits;
    uint32_t *address;
    uint32_t address_bits;
    uint32_t *tx;
    uint32_t tx_bits;
    uint32_t *rx;
    uint32_t rx_bits;
    uint32_t dummy_bits;
} rom_spi_command;
_Static_assert(sizeof(rom_spi_command) == 32, "Unexpected RV32 ROM SPI ABI");
_Static_assert(__builtin_offsetof(rom_spi_command, rx) == 20, "Unexpected RX pointer offset");
extern void rom_spi_config(int spi, rom_spi_command *config);
extern void rom_spi_start(int spi, uint8_t *rx, uint16_t length,
                          uint8_t cs_mask, _Bool write_or_erase);
extern void rom_spi_mode(int spi, int mode);
extern void rom_select_qio_pins(uint8_t wp, uint32_t flash_gpio_config);

#define REG32(addr) (*(volatile uint32_t *)(addr))
#define SPI1_CTRL REG32(0x60003008u)
#define SPI1_CLOCK REG32(0x60003014u)
#define SPI1_MISC REG32(0x60003034u)
#define SPI0_SMEM_AC REG32(0x600021a0u)
#define CS0_DISABLED 1u
#define CS1_DISABLED 2u

static void progress(uint32_t value)
{
    stage = value;
    put_string("\r\nC5 LOAD STAGE=");
    put_hex(stage);
    put_string("\r\n");
}

static uint32_t psram_command(uint16_t command, uint32_t address_bits,
                              uint32_t *rx, uint32_t rx_bits, uint32_t quad, uint32_t chip_select)
{
    if (chip_select > 1 || (command != 0x9f && command != 0xf5)
            || (chip_select == 0 && (command != 0x9f || address_bits != 0 || quad)))
        return 0x400u;
    uint32_t address = 0;
    rom_spi_command config = {
        .command = command, .command_bits = 8,
        .address = &address, .address_bits = address_bits,
        .tx = (uint32_t *)0, .tx_bits = 0,
        .rx = rx, .rx_bits = rx_bits, .dummy_bits = 0,
    };
    /* Follow the official QPI-exit/SPI-ID transaction mode choices. */
    rom_spi_mode(1, quad ? 0 : 5);  /* QIO=0, SLOWRD=5 in pinned ROM enum. */
    if (quad) SPI1_CTRL |= 1u << 8;  /* fcmd_quad */
    else SPI1_CTRL &= ~(1u << 8);
    SPI1_CLOCK = test_spi_clock;
    SPI1_MISC = (SPI1_MISC & ~3u) | (chip_select == 1 ? CS0_DISABLED : CS1_DISABLED);
    rom_spi_config(1, &config);
    /* Exactly one selected device: only ID commands are sent to Flash/CS0. */
    SPI1_MISC = (SPI1_MISC & ~3u) | (chip_select == 1 ? CS0_DISABLED : CS1_DISABLED);
    __asm__ volatile ("fence iorw, iorw" ::: "memory");
    if ((SPI1_MISC & 3u) != (chip_select == 1 ? CS0_DISABLED : CS1_DISABLED)) return 0x20u;
    if (command == 0x9f) snapshot(chip_select ? "cs1-id-config" : "cs0-id-config");
    /* ROM helper has no software deadline: stage logs identify a stalled call. */
    rom_spi_start(1, (uint8_t *)rx, (uint16_t)(rx_bits / 8), (uint8_t)(1u << chip_select), 0);
    return 0;
}

static void snapshot(const char *label)
{
    put_string("\r\nC5 LOAD SNAP "); put_string(label);
    put_string(" mux15="); put_hex(REG32(0x6009003cu));
    put_string(" mux18="); put_hex(REG32(0x60090048u));
    put_string(" mux20="); put_hex(REG32(0x60090050u));
    put_string(" ctrl="); put_hex(SPI1_CTRL);
    put_string(" user="); put_hex(REG32(0x60003018u));
    put_string(" user1="); put_hex(REG32(0x6000301cu));
    put_string(" user2="); put_hex(REG32(0x60003020u));
    put_string(" addr="); put_hex(REG32(0x60003004u));
    put_string(" misc="); put_hex(SPI1_MISC);
    put_string(" clk="); put_hex(SPI1_CLOCK);
    put_string(" sclk="); put_hex(REG32(0x60002050u));
    put_string(" smem_ac="); put_hex(SPI0_SMEM_AC);
    put_string("\r\n");
}

static uint32_t read_psram_id(void)
{
    rom_eco = REG32(0x40000014u);
    if (rom_eco != 2) return 0x10u;
    progress(1);
    saved_spi_clock = SPI1_CLOCK;
    saved_smem_ac = SPI0_SMEM_AC;
    test_spi_clock = (15u << 16) | (7u << 8) | 15u;
    snapshot("entry");
    /* Positive control: a read-only Flash ID before PSRAM GPIO/config changes. */
    uint32_t result = psram_command(0x9f, 0, &flash_ids[0], 24, 0, 0);
    if (result) return result;
    flash_ids[0] &= 0xffffffu;
    snapshot("flash-control");
    /* Same documented C5 pin selection as the first failed ID probe. */
    REG32(0x6009003cu) &= ~(7u << 12);
    rom_select_qio_pins(18, 0);
    progress(2);
    result = psram_command(0xf5, 0, (uint32_t *)0, 0, 1, 1);
    if (result) return result;
    result = psram_command(0x9f, 24, &before_id, 24, 0, 1);
    if (result) return result;
    before_id &= 0xffffffu;
    snapshot("before-cs-timing");
    /* Official psram_set_cs_timing: setup=hold=1, encoded time fields=0. */
    configured_smem_ac = (saved_smem_ac & ~0xfffu) | 3u;
    SPI0_SMEM_AC = configured_smem_ac;
    __asm__ volatile ("fence iorw, iorw" ::: "memory");
    snapshot("with-cs-timing");
    result = psram_command(0xf5, 0, (uint32_t *)0, 0, 1, 1);
    if (result) return result;
    progress(3);
    for (uint32_t i = 0; i < 3; ++i) {
        result = psram_command(0x9f, 24, &psram_ids[i], 24, 0, 1);
        if (result) return result;
        psram_ids[i] &= 0xffffffu;
        ++completed_reads;
    }
    snapshot("after-psram-id");
    progress(4);
    result = psram_command(0x9f, 24, extended_id, 48, 0, 1);
    if (result) return result;
    ++completed_reads;
    /* Repeat Flash control to detect loss of the shared command path. */
    result = psram_command(0x9f, 0, &flash_ids[1], 24, 0, 0);
    if (result) return result;
    flash_ids[1] &= 0xffffffu;
    snapshot("final-flash-control");
    SPI1_CLOCK = saved_spi_clock;
    SPI0_SMEM_AC = saved_smem_ac;
    progress(5);
    if (psram_ids[0] != psram_ids[1] || psram_ids[0] != psram_ids[2]
            || psram_ids[0] != (extended_id[0] & 0xffffffu)) return 0x40u;
    if (((psram_ids[0] >> 8) & 0xffu) != 0x5du) return 0x80u;
    if ((psram_ids[0] & 0xffu) != 0x0du) return 0x100u;
    uint32_t density = (psram_ids[0] >> 21) & 7u;
    if (density <= 2) nominal_size = 0x200000u << density;
    else if (((psram_ids[0] >> 16) & 0xffu) == 0x26u) nominal_size = 0x800000u;
    else return 0x200u;
    return 0;
}

#define BASE 0x42000000u
#define DT_OFF 0x7f0000u
#define MAX_IMAGE 0x200000u
#define MMU_INDEX REG32(0x60002380u)
#define MMU_CONTENT REG32(0x6000237cu)
static uint32_t header[16], packet[19], verify[16];
static uint32_t sequence;
static __attribute__((noreturn)) void fatal(uint32_t code)
{
    for (;;) {
        put_string("\r\nC5 LOAD FATAL code="); put_hex(code);
        put_string(" seq="); put_hex(sequence); put_string("\r\n"); delay();
    }
}
static uint32_t fnv(uint32_t state, const uint8_t *p, uint32_t n)
{
    for (uint32_t i=0; i<n; ++i) state=(state^p[i])*16777619u;
    return state;
}
static void receive(void *dest, uint32_t n)
{
    uint8_t *p=dest;
    for (uint32_t i=0;i<n;++i) {
        uint32_t budget=100000000u;
        while (!(USB_EP1_CONF & 4u)) if (!--budget) fatal(1);
        p[i]=(uint8_t)USB_EP1;
    }
}
static uint32_t valid_header(const uint32_t *h)
{
    if (h[0]!=0x354c444bu || h[1]!=1 || h[2]<64 || h[2]>MAX_IMAGE
        || (h[2]&63) || h[3]<64 || h[3]>0x10000u || (h[3]&63)) return 0;
    for (uint32_t i=6;i<16;++i) if (h[i]) return 0;
    return 1;
}
static uint32_t valid_packet(const uint32_t *p, uint32_t seq)
{
    return p[0]==seq && p[2]==64 && fnv(2166136261u,(const uint8_t *)(p+3),64)==p[1];
}
static uint32_t allowed_offset(uint32_t off)
{
    return !(off&63) && ((off<header[2]) || (off>=DT_OFF && off<DT_OFF+header[3]));
}
static void transfer(uint32_t write, uint32_t off, uint32_t *words)
{
    if (!allowed_offset(off) || write>1) fatal(2);
    rom_spi_command c={.command=write?2:3,.command_bits=8,.address=&off,.address_bits=24,
        .tx=write?words:0,.tx_bits=write?512:0,.rx=write?0:words,.rx_bits=write?0:512};
    rom_spi_mode(1,5); SPI1_CTRL &= ~(1u<<8); SPI1_CLOCK=test_spi_clock;
    SPI1_MISC=(SPI1_MISC|1u)&~2u; rom_spi_config(1,&c);
    SPI1_MISC=(SPI1_MISC|1u)&~2u;
    if ((SPI1_MISC&3)!=1) fatal(3);
    __asm__ volatile("fence iorw,iorw" ::: "memory");
    rom_spi_start(1,write?0:(uint8_t *)words,write?0:64,2,0);
}
static void send_ack(uint32_t seq)
{
    /* One <=64-byte FIFO packet per ACK, not one USB packet per character. */
    uint8_t ack[22];
    const char *prefix="C5 LOAD ACK ", *digits="0123456789abcdef";
    for(uint32_t i=0;i<12;++i)ack[i]=(uint8_t)prefix[i];
    for(uint32_t i=0;i<8;++i)ack[12+i]=(uint8_t)digits[(seq>>(28-4*i))&15];
    ack[20]='\r';ack[21]='\n';
    uint32_t budget=1000000;
    while(!(USB_EP1_CONF&USB_TX_FREE))if(!--budget)fatal(24);
    for(uint32_t i=0;i<sizeof(ack);++i)USB_EP1=ack[i];
    USB_EP1_CONF=USB_WR_DONE;
}
static void load_segment(uint32_t off,uint32_t length,uint32_t expected)
{
    uint32_t hash=2166136261u;
    for (uint32_t pos=0;pos<length;pos+=64) {
        receive(packet,sizeof(packet));
        if (!valid_packet(packet,sequence)) fatal(4);
        transfer(1,off+pos,packet+3); transfer(0,off+pos,verify);
        for (uint32_t i=0;i<16;++i) if (verify[i]!=packet[i+3]) fatal(5);
        hash=fnv(hash,(uint8_t *)verify,64);
        send_ack(sequence++);
    }
    if (hash!=expected) fatal(6);
}
static void map_psram(void)
{
    /* Enter QPI with the proven CS1-only command sequence. */
    uint32_t address=0;
    rom_spi_command c={.command=0x35,.command_bits=8,.address=&address};
    rom_spi_mode(1,5);SPI1_CTRL &= ~(1u<<8);SPI1_CLOCK=test_spi_clock;
    SPI1_MISC=(SPI1_MISC|1u)&~2u;rom_spi_config(1,&c);SPI1_MISC=(SPI1_MISC|1u)&~2u;
    if ((SPI1_MISC&3)!=1) fatal(7);
    rom_spi_start(1,0,0,2,0);
    rom_cache_boot_init();(void)rom_cache_disable();
    REG32(0x60002050u)=test_spi_clock;
    REG32(0x60002040u)=(REG32(0x60002040u)&~((1u<<0)|(1u<<1)|(1u<<3)|(0x3fu<<6)|(0x3fu<<14)))
        |(1u<<2)|(1u<<4)|(1u<<5)|(1u<<20)|(5u<<6)|(23u<<14);
    REG32(0x60002048u)=(REG32(0x60002048u)&~0xf000ffffu)|(7u<<28)|0xebu;
    REG32(0x6000204cu)=(REG32(0x6000204cu)&~0xf000ffffu)|(7u<<28)|0x38u;
    for (uint32_t page=0;page<128;++page) {
        MMU_INDEX=page;MMU_CONTENT=0x600u|page;
        if(MMU_CONTENT!=(0x600u|page))fatal(8);
    }
    uint32_t cfg,addr;
    __asm__ volatile("csrr %0,0xbcf;csrr %1,0xbdf":"=r"(cfg),"=r"(addr));
    if(cfg!=0xc0000011u||addr!=0x10bfffffu)fatal(9);
    cfg|=12u;__asm__ volatile("csrw 0xbcf,%0;fence iorw,iorw"::"r"(cfg):"memory");
    __asm__ volatile("csrr %0,0xbcf":"=r"(cfg));if(cfg!=0xc000001du)fatal(10);
    rom_cache_enable(0);REG32(0x600c8004u)&=~3u;
    __asm__ volatile("fence iorw,iorw;fence.i":::"memory");
    if(REG32(0x600c8004u)&3u)fatal(11);
}
__attribute__((noreturn)) void diagnostic_main(void)
{
    if(cookie!=0x43354c44u || read_psram_id()) fatal(12);
    uint32_t packed=(extended_id[0]>>16)|(extended_id[1]<<16);
    uint32_t eid=__builtin_bswap32(packed);
    if(psram_ids[0]!=0x5a5d0du || flash_ids[0]!=0x174020u || flash_ids[1]!=0x174020u
       || nominal_size!=0x800000u || ((eid>>25)&1u)==0 || ((eid>>5)&0xfffffu)==0x8a445u)fatal(13);
    put_string("\r\nC5 LOAD READY v1\r\n");receive(header,sizeof(header));
    if(!valid_header(header))fatal(14);
    SPI0_SMEM_AC=configured_smem_ac;
    put_string("\r\nC5 LOAD BEGIN\r\n");
    load_segment(0,header[2],header[4]);load_segment(DT_OFF,header[3],header[5]);
    map_psram();
    if(fnv(2166136261u,(const uint8_t *)BASE,header[2])!=header[4]
       ||fnv(2166136261u,(const uint8_t *)(BASE+DT_OFF),header[3])!=header[5])fatal(15);
    if(REG32(BASE+56)!=0x05435352u || REG32(BASE+DT_OFF)!=0xedfe0dd0u)fatal(16);
    /* Bootstrap runs only M-mode: require ROM PMP cfg0 still unlocked/empty. */
    uint32_t pmp;__asm__ volatile("csrr %0,pmpcfg0":"=r"(pmp));if(pmp)fatal(17);
    uint32_t lines=REG32(0x20800004u)&0x1fffu;if(!lines||lines>128)fatal(18);
    for(uint32_t i=0;i<lines;++i) {
        *(volatile uint8_t *)(0x20801001u+4*i)=0;
        if(*(volatile uint8_t *)(0x20801001u+4*i))fatal(19);
    }
    uint32_t clic=REG32(0x20800000u);if(clic!=6)fatal(20);
    REG32(0x20800000u)=(clic&~0x30u)|0x10u;
    if(REG32(0x20800000u)!=0x16u)fatal(21);
    put_string("\r\nC5 LOAD READY-JUMP entry=42000000 dt=427f0000\r\n");
    uint32_t go;receive(&go,4);if(go!=0x21214f47u)fatal(22);
    put_string("\r\nC5 LOAD JUMP\r\n");
    __asm__ volatile("csrci mstatus,8;fence iorw,iorw;fence.i;li a0,0;li a1,0x427f0000;li t0,0x42000000;jr t0":::"memory");
    __builtin_unreachable();
}
__attribute__((noreturn)) void diagnostic_trap(uint32_t cause,uint32_t pc,uint32_t tval)
{
    put_string("\r\nC5 LOAD TRAP cause=");put_hex(cause);put_string(" pc=");put_hex(pc);
    put_string(" tval=");put_hex(tval);fatal(23);
}
