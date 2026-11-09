#!/usr/bin/env python3
"""Host-only custom C5 ROM boot image and explicit raw Flash payload layout."""
import hashlib,json,re,struct,subprocess,sys
from pathlib import Path
import esptool
from esptool.bin_image import LoadFirmwareImage
ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parent
BUILD=ROOT/'build'

def fnv(data):
    value=2166136261
    for b in data:value=((value^b)*16777619)&0xffffffff
    return value

def pad(data):return data+bytes((-len(data))%64)

def verify(root,manifest):
    for line in manifest.read_text().splitlines():
        expected,name=line.split(maxsplit=1)
        if hashlib.sha256((root/name).read_bytes()).hexdigest()!=expected:
            raise ValueError('Stale input manifest: '+name)

def main():
    if esptool.__version__!='5.6.0':raise ValueError('Requires reviewed esptool5.6.0')
    verify(PROJECT/'kernel',PROJECT/'kernel/build/SHA256SUMS')
    verify(PROJECT/'bootloader/psram',PROJECT/'bootloader/psram/build/SHA256SUMS')
    BUILD.mkdir(exist_ok=True)
    kernel=pad((PROJECT/'bootloader/psram/build/Image').read_bytes())
    dt=pad((PROJECT/'bootloader/psram/build/c5-early.dtb').read_bytes())
    fsroot=PROJECT/'user/rootfs'
    verify(fsroot,fsroot/'build/SHA256SUMS')
    rootfs=pad((fsroot/'build/rootfs.cramfs').read_bytes())
    if not 64<=len(rootfs)<=0xf0000 or rootfs[:4]!=b'\x45\x3d\xcd\x28':
        raise ValueError('Rootfs must be little-endian cramfs fitting reserved PSRAM')
    if not 64<=len(kernel)<=0x200000 or not 64<=len(dt)<=0x10000:raise ValueError('Payload size')
    if kernel[56:60]!=b'RSC\x05' or dt[:4]!=b'\xd0\x0d\xfe\xed':raise ValueError('Payload magic')
    config=(PROJECT/'kernel/build/.config').read_text()
    if 'CONFIG_INITRAMFS_SOURCE=""\n' not in config or 'root=mtd:rootfs rootfstype=cramfs ro init=/init' not in config:
        raise ValueError('Kernel is not configured for the independent cramfs root')
    tree=subprocess.check_output([str(PROJECT/'kernel/build/scripts/dtc/dtc'),
                                  '-I','dtb','-O','dts','-'],input=dt).decode()
    nodes=re.findall(r'rootfs@42700000\s*\{([^{}]*)\}',tree,re.S)
    if len(nodes)!=2 or any(not re.search(r'reg\s*=\s*<0x42700000 0xf0000>',n) for n in nodes):
        raise ValueError('DT rootfs mapping/reservation differs from loader bounds')
    if not any('no-map;' in n for n in nodes) or not any('"mtd-rom"' in n and '"rootfs"' in n for n in nodes):
        raise ValueError('DT must reserve rootfs PSRAM and expose a read-only MTD named rootfs')
    original=(PROJECT/'bootloader/psram/main.c').read_text()
    guard='if(cfg!=0xc0000011u||addr!=0x10bfffffu)fatal(9);'
    if original.count(guard)!=1:raise ValueError('Upstream PMA guard changed; review adaptation')
    # Boot-only adaptation; the hardware-qualified USB source is unchanged.
    adapted=original.replace(guard,'if(!flash_pma_valid(cfg,addr))fatal(9);')
    offset_guard='return !(off&63) && ((off<header[2]) || (off>=DT_OFF && off<DT_OFF+header[3]));'
    if original.count(offset_guard)!=1:raise ValueError('Upstream transfer bounds changed; review rootfs adaptation')
    adapted=adapted.replace(offset_guard,
        'return !(off&63) && ((off<header[2]) || (off>=DT_OFF && off<DT_OFF+header[3]) || '
        '(header[1]==2 && header[6]>=64 && header[6]<=0xf0000u && !(header[6]&63) && '
        'off>=0x700000u && off-0x700000u<=header[6]-64));')
    (BUILD/'psram-loader.inc').write_text(adapted)
    if '--prepare' in sys.argv:
        return
    image=LoadFirmwareImage('esp32c5',(BUILD/'bootloader.bin').read_bytes());image.verify()
    if image.entrypoint!=0x40800000 or image.stored_digest!=image.calc_digest or image.checksum!=image.calculate_checksum():raise ValueError('ROM image integrity')
    for segment in image.segments:
        if not 0x40800000<=segment.addr<segment.addr+len(segment.data)<=0x40806000:raise ValueError('SRAM segment bounds')
    if (BUILD/'bootloader.bin').stat().st_size>0x6000:raise ValueError('Boot image overlaps existing partition-table sector')
    (BUILD/'kernel.bin').write_bytes(kernel);(BUILD/'dtb.bin').write_bytes(dt)
    (BUILD/'rootfs.cramfs').write_bytes(rootfs)
    (BUILD/'metadata.bin').write_bytes(struct.pack('<8I',0x354c444b,2,len(kernel),len(dt),fnv(kernel),fnv(dt),len(rootfs),fnv(rootfs))+bytes(32))
    regions=[]
    for offset,name in [(0x2000,'bootloader.bin'),(0x10000,'metadata.bin'),(0x20000,'kernel.bin'),(0x220000,'dtb.bin'),(0x230000,'rootfs.cramfs')]:
        data=(BUILD/name).read_bytes()
        regions.append(dict(offset=offset,file=name,bytes=len(data),erase_start=offset,erase_end=(offset+len(data)+4095)&~4095,sha256=hashlib.sha256(data).hexdigest()))
    for a,b in zip(regions,regions[1:]):
        if a['erase_end']>b['offset']:raise ValueError('Sector overlap')
    if regions[-1]['erase_end']>0x320000:raise ValueError('Rootfs overlaps reserved free space')
    plan=dict(chip='esp32c5',flash_bytes=0x800000,rom_boot_offset=0x2000,hardware_tested=False,
        storage=dict(rootfs='cramfs',rootfs_psram=0x42700000,rootfs_reserved_bytes=0xf0000,
                     persistent_storage=False,data_backend='experimental-sram-nor-jffs2',
                     data_write_enabled='c5_flash_write=1' in config,
                     data_candidate_start=0x400000,data_candidate_bytes=0x400000),
        custom_layout=True,partition_table_used=False,flash_written=False,regions=regions,
        warning='Overwrites existing bootloader/firmware data in listed sectors. No OTA/security/rollback. Existing Linux remains a bounded diagnostic kernel.')
    (BUILD/'plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    inputs=[PROJECT/'Makefile',ROOT/'Makefile',ROOT/'main.c',ROOT/'start.S',ROOT/'memory.ld',ROOT/'rom-functions.ld',ROOT/'build.py',ROOT/'review.py',ROOT/'preflight.sh',ROOT/'flash.py',ROOT/'capture.py',ROOT/'console.py',
        PROJECT/'bootloader/psram/main.c']
    ref=PROJECT/'reference/esp-idf-v5.5.1'
    inputs += [ref/n for n in ['components/riscv/include/riscv/csr.h','components/soc/esp32c5/register/soc/lp_wdt_reg.h',
        'components/soc/esp32c5/register/soc/timer_group_reg.h','components/soc/esp32c5/register/soc/reg_base.h',
        'components/hal/esp32c5/include/hal/lpwdt_ll.h','components/esp_rom/esp32c5/ld/esp32c5.rom.ld']]
    inputs += [fsroot/'build/rootfs.cramfs',fsroot/'build/SHA256SUMS']
    inputs += [BUILD/n for n in ['bootloader.bin','kernel.bin','dtb.bin','rootfs.cramfs','metadata.bin','bootloader.elf','plan.json','psram-loader.inc']]
    import os
    (BUILD/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+os.path.relpath(p,ROOT)+'\n' for p in inputs))
    for r in regions:print(f"{r['file']}: offset={r['offset']:#x}, bytes={r['bytes']}, sectors={r['erase_start']:#x}..{r['erase_end']:#x} (end exclusive)")
    print('HOST ONLY: candidate built; no serial connection or Flash write. Cold boot untested.')
if __name__=='__main__':main()
