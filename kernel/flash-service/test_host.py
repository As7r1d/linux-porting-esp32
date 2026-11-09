#!/usr/bin/env python3
"""Native destructive-command policy and SRAM artifact checks; no board access."""
from pathlib import Path
import subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parent
class Checks(unittest.TestCase):
    def test_partition_page_and_authorization_boundaries(self):
        source='''#include "abi.h"
int main(void){
 struct c5_request r={.op=C5_OP_PROGRAM,.offset=0x400000,.length=64};
 if(c5_request_valid(&r))return 1;
 r.key=C5_WRITE_KEY;if(!c5_request_valid(&r))return 2;
 r.offset=0x3fffff;if(c5_request_valid(&r))return 3;
 r.offset=0x7fffc0;if(!c5_request_valid(&r))return 4;
 r.offset=0x7fffc1;if(c5_request_valid(&r))return 5;
 r.offset=0x4000ff;if(c5_request_valid(&r))return 6;
 r.length=1;if(!c5_request_valid(&r))return 7;
 r.op=C5_OP_ERASE;r.offset=0x7ff000;r.length=4096;
 if(!c5_request_valid(&r))return 8;
 r.offset++;if(c5_request_valid(&r))return 9;
 r.offset=0xfffff000;if(c5_request_valid(&r))return 10;
 r.offset=0x400000;r.length=0;if(c5_request_valid(&r))return 11;
 r.op=99;if(c5_request_valid(&r))return 12;
 return 0;}
'''
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'t.c').write_text(source)
            subprocess.run(['cc','-Wall','-Wextra','-Werror','-I',str(ROOT),str(p/'t.c'),'-o',str(p/'t')],check=True)
            subprocess.run([str(p/'t')],check=True)
    def test_linked_sram_image(self):
        b=(ROOT/'build/service.bin').read_bytes()
        self.assertTrue(0<len(b)<=8192)
        prefix='riscv64-unknown-elf-'
        symbols=subprocess.check_output([prefix+'nm','-n',str(ROOT/'build/service.elf')],text=True)
        self.assertIn('40808000 T _entry',symbols)
        self.assertEqual(subprocess.check_output([prefix+'nm','-u',str(ROOT/'build/service.elf')],text=True),'')
        dis=subprocess.check_output([prefix+'objdump','-d',str(ROOT/'build/service.elf')],text=True)
        self.assertNotIn('(gp)',dis)
        self.assertIn('4080e',dis) # private SRAM stack
if __name__=='__main__':unittest.main()
