#!/usr/bin/env python3
"""Compile C+libc with real elf2flt and inspect outputs; never execute on board."""
from pathlib import Path
import hashlib,json,struct,subprocess
ROOT=Path(__file__).resolve().parent
HOST=ROOT/'output/host/bin'
PREFIX='riscv32-buildroot-linux-uclibc-'
cc=HOST/(PREFIX+'gcc')
for tool in ['gcc','elf2flt','flthdr','readelf']:
    if not (HOST/(PREFIX+tool)).exists():raise SystemExit('Toolchain not built: '+tool)
build=ROOT/'smoke';build.mkdir(exist_ok=True)
flags=['-D_LARGEFILE_SOURCE','-D_LARGEFILE64_SOURCE','-D_FILE_OFFSET_BITS=64','-Os','-march=rv32imac_zicsr_zifencei','-mabi=ilp32','-fPIC','-fno-stack-protector','-static','-Wall','-Wextra','-Werror','-Wl,-elf2flt=-r','-Wl,-elf2flt=-s16384']
subprocess.run([str(cc),*flags,str(ROOT/'c-smoke.c'),'-o',str(build/'c-smoke')],check=True)
raw=(build/'c-smoke').read_bytes()
if len(raw)<64:raise SystemExit('Truncated output')
h=struct.unpack('>4s15I',raw[:64])
if h[0]!=b'bFLT' or h[1]!=4:raise SystemExit('Compiler did not produce revision4 bFLT')
if not 64<=h[2]<h[3]<=h[4]<=h[5]<0x400000:raise SystemExit('Invalid entry/text/data/bss bounds')
if h[6]!=16384 or not h[8] or h[7]<h[4] or h[7]+4*h[8]>len(raw) or h[9]&~3:
    raise SystemExit('Unexpected stack/relocation/flat flags')
# Require the retained ELF companion too: verify RV32, little endian, soft float.
elfs=[p for p in build.glob('c-smoke*') if p.is_file() and p.read_bytes()[:4]==b'\x7fELF']
if len(elfs)!=1:raise SystemExit('Expected exactly one retained ELF companion')
elf=elfs[0].read_bytes()
if elf[4:6]!=b'\x01\x01' or struct.unpack_from('<H',elf,18)[0]!=243 or struct.unpack_from('<I',elf,36)[0]&6:
    raise SystemExit('Wrong ELF class/endianness/machine/float ABI')
info=subprocess.check_output([str(HOST/(PREFIX+'readelf')),'-h','-A',str(elfs[0])],text=True)
(build/'elf-report.txt').write_text(info)
version=subprocess.check_output([str(cc),'--version'],text=True).splitlines()[0]
report=dict(compiler=version,flags=flags,flat_fields=dict(zip(['revision','entry','data_start','data_end','bss_end','stack_size','reloc_start','reloc_count','flags','build_date'],h[1:11])),hardware_tested=False)
(build/'report.json').write_text(json.dumps(report,indent=2)+'\n')
with (build/'SHA256SUMS').open('w') as out:
    for p in [ROOT/'c5_defconfig',ROOT/'uclibc.fragment',ROOT/'build.sh',ROOT/'smoke.py',ROOT/'c-smoke.c',ROOT/'output/.config',build/'c-smoke',elfs[0],build/'elf-report.txt',build/'report.json']:
        out.write(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(ROOT)}\n')
print('PASS: C/libc and real elf2flt produced RV32 soft-float bFLT with relocations')
print('Host compilation/format checks only; C program not executed on C5.')
