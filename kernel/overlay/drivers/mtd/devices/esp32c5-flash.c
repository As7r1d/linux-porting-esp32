// SPDX-License-Identifier: GPL-2.0
/* Experimental data-only NOR backend. Kernel/rootfs remain in PSRAM.
 * No SPI Flash cache mapping is created. No writes without c5_flash_write=1.
 */
#include <linux/init.h>
#include <linux/module.h>
#include <linux/sched.h>
#include <linux/io.h>
#include <linux/jiffies.h>
#include <linux/delay.h>
#include <linux/mtd/mtd.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/slab.h>
#include <linux/irqflags.h>
#include "c5-flash-abi.h"
#include "c5-flash-service.h"

struct c5_flash {
    struct mtd_info mtd;
    struct mutex lock;
    void __iomem *sram;
    bool failed;
};
static bool write_enabled;
static int __init c5_write_param(char *value)
{
    return kstrtobool(value,&write_enabled);
}
early_param("c5_flash_write",c5_write_param);

/* Caller holds lock; PSRAM is accessed only before/after SRAM execution. */
static int command(struct c5_flash *f, unsigned int op, u32 offset,
                   unsigned int length, void *data)
{
    struct c5_request request={.op=op,.offset=offset,.length=length,
                              .key=write_enabled?C5_WRITE_KEY:0};
    unsigned long flags;
    void __iomem *slot=f->sram+(C5_REQUEST_BASE-C5_SERVICE_BASE);
    if (f->failed) return -EIO;
    if (op==C5_OP_PROGRAM) memcpy(request.data,data,length);
    if (!c5_request_valid(&request)) return -EINVAL;
    local_irq_save(flags);
    memcpy_toio(slot,&request,sizeof(request));
    wmb();
    ((void (*)(void))C5_SERVICE_BASE)();
    mb();
    memcpy_fromio(&request,slot,sizeof(request));
    local_irq_restore(flags);
    if (request.result) return request.result;
    if (op==C5_OP_READ) memcpy(data,request.data,length);
    if (op==C5_OP_STATUS || op==C5_OP_ID) *(u32 *)data=request.data[0];
    return 0;
}

static int wait_ready(struct c5_flash *f)
{
    unsigned long deadline=jiffies+5*HZ;
    u32 status;
    int ret;
    do {
        ret=command(f,C5_OP_STATUS,0,0,&status);
        if (ret) return ret;
        if (!(status&1)) return 0;
        usleep_range(1000,2000);
    } while (time_before(jiffies,deadline));
    return -ETIMEDOUT;
}

static int read_locked(struct c5_flash *f, loff_t from, size_t len, u8 *buffer)
{
    int ret;
    while (len) {
        unsigned int n=min_t(size_t,len,64);
        ret=command(f,C5_OP_READ,C5_DATA_BASE+(u32)from,n,buffer);
        if (ret) return ret;
        from+=n;buffer+=n;len-=n;
        cond_resched();
    }
    return 0;
}
static bool valid_range(loff_t offset, size_t length)
{
    return offset>=0 && offset<C5_DATA_BYTES && length<=C5_DATA_BYTES-offset;
}
static int c5_read(struct mtd_info *mtd, loff_t from, size_t length,
                   size_t *retlen, u_char *buffer)
{
    struct c5_flash *f=container_of(mtd,struct c5_flash,mtd);
    int ret;
    *retlen=0;
    if (!length) return 0;
    if (!valid_range(from,length)) return -EINVAL;
    mutex_lock(&f->lock);
    ret=read_locked(f,from,length,buffer);
    if (!ret) *retlen=length;
    mutex_unlock(&f->lock);
    return ret;
}
static int c5_write(struct mtd_info *mtd, loff_t to, size_t length,
                    size_t *retlen, const u_char *buffer)
{
    struct c5_flash *f=container_of(mtd,struct c5_flash,mtd);
    u8 verify[64];
    int ret=0;
    *retlen=0;
    if (!write_enabled) return -EROFS;
    if (!length) return 0;
    if (!valid_range(to,length)) return -EINVAL;
    mutex_lock(&f->lock);
    while (length) {
        unsigned int n=min_t(size_t,length,min_t(size_t,64,256-(to&255)));
        ret=command(f,C5_OP_PROGRAM,C5_DATA_BASE+(u32)to,n,(void *)buffer);
        if (!ret) ret=wait_ready(f);
        if (!ret) ret=read_locked(f,to,n,verify);
        if (!ret && memcmp(buffer,verify,n)) ret=-EIO;
        if (ret) { f->failed=true; break; }
        *retlen+=n;to+=n;buffer+=n;length-=n;
        cond_resched();
    }
    mutex_unlock(&f->lock);
    return ret;
}
static int c5_erase(struct mtd_info *mtd, struct erase_info *erase)
{
    struct c5_flash *f=container_of(mtd,struct c5_flash,mtd);
    u8 verify[64];
    u64 address,end=erase->addr+erase->len;
    int ret=0;
    if (!write_enabled) return -EROFS;
    if (!erase->len || erase->addr>=C5_DATA_BYTES || erase->len>C5_DATA_BYTES ||
        !valid_range(erase->addr,erase->len) ||
        ((erase->addr|erase->len)&4095)) return -EINVAL;
    mutex_lock(&f->lock);
    for (address=erase->addr;address<end;address+=4096) {
        ret=command(f,C5_OP_ERASE,C5_DATA_BASE+(u32)address,4096,NULL);
        if (!ret) ret=wait_ready(f);
        for (unsigned int off=0;!ret && off<4096;off+=64) {
            ret=read_locked(f,address+off,64,verify);
            if (!ret && memchr_inv(verify,255,64)) ret=-EIO;
        }
        if (ret) { erase->fail_addr=address;f->failed=true;break; }
        cond_resched();
    }
    mutex_unlock(&f->lock);
    return ret;
}
static int c5_probe(struct platform_device *pdev)
{
    struct c5_flash *f;
    struct resource *res;
    u32 id=0,hash=2166136261u;
    u8 sample[64];
    int ret;
    res=platform_get_resource(pdev,IORESOURCE_MEM,0);
    if (!res || res->start!=C5_SERVICE_BASE || resource_size(res)!=C5_SERVICE_BYTES)
        return -EINVAL;
    f=devm_kzalloc(&pdev->dev,sizeof(*f),GFP_KERNEL);
    if (!f) return -ENOMEM;
    mutex_init(&f->lock);
    f->sram=devm_ioremap_resource(&pdev->dev,res);
    if (IS_ERR(f->sram)) return PTR_ERR(f->sram);
    /* Sole SPI1 owner at runtime; SPI0's PSRAM configuration is never changed. */
    res=platform_get_resource(pdev,IORESOURCE_MEM,1);
    if (!res || res->start!=0x60003000 || resource_size(res)!=0x400) return -EINVAL;
    if (!devm_request_mem_region(&pdev->dev,res->start,resource_size(res),dev_name(&pdev->dev))) return -EBUSY;
    if (sizeof(c5_service_image)>C5_REQUEST_BASE-C5_SERVICE_BASE) return -EFBIG;
    memcpy_toio(f->sram,c5_service_image,sizeof(c5_service_image));
    for (unsigned int i=0;i<sizeof(c5_service_image);++i)
        if (readb(f->sram+i)!=c5_service_image[i]) return -EIO;
    __asm__ volatile("fence iorw,iorw;fence.i":::"memory");
    ret=command(f,C5_OP_ID,0,0,&id);
    if (!ret && id!=C5_FLASH_ID) ret=-ENODEV;
    if (!ret) ret=read_locked(f,0,sizeof(sample),sample);
    if (ret) return dev_err_probe(&pdev->dev,ret,"SRAM Flash read probe failed\n");
    for (unsigned int i=0;i<sizeof(sample);++i) hash=(hash^sample[i])*16777619u;
    f->mtd=(struct mtd_info){.name="data",.type=MTD_NORFLASH,
        .flags=write_enabled?MTD_CAP_NORFLASH:(MTD_CAP_NORFLASH&~MTD_WRITEABLE),
        .size=C5_DATA_BYTES,.erasesize=4096,.writesize=1,.writebufsize=64,
        ._read=c5_read,._write=c5_write,._erase=c5_erase,.owner=THIS_MODULE};
    f->mtd.dev.parent=&pdev->dev;
    ret=mtd_device_register(&f->mtd,NULL,0);
    if (ret) return ret;
    platform_set_drvdata(pdev,f);
    dev_notice(&pdev->dev,"C5 FLASH READY id=%06x offset=00400000 bytes=00400000 write=%u sample_fnv=%08x\n",id,write_enabled,hash);
    return 0;
}
static const struct of_device_id c5_of_match[]={
    {.compatible="experimental,esp32c5-data-flash"},{}
};
static struct platform_driver c5_driver={.probe=c5_probe,
    .driver={.name="esp32c5-data-flash",.of_match_table=c5_of_match,.suppress_bind_attrs=true}};
builtin_platform_driver(c5_driver);
