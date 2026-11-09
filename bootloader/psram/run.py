#!/usr/bin/env python3
"""RAM-only Linux BusyBox hush PID1 checkpoint loader; overwrites PSRAM kernel/DT load regions."""
import argparse, contextlib, hashlib, re, struct, sys, time, secrets
import os, select, termios, tty
from pathlib import Path
from datetime import datetime, timezone
import esptool, serial
from esptool.bin_image import LoadFirmwareImage
from esptool.cmds import load_ram
from esptool.targets.esp32c5 import ESP32C5ROM

class Tee:
    def __init__(self, terminal, transcript):
        self.terminal = terminal
        self.transcript = transcript

    def write(self, text):
        self.terminal.write(text)
        self.transcript.write(text)
        self.flush()
        return len(text)

    def flush(self):
        self.terminal.flush()
        self.transcript.flush()

    def isatty(self):
        return False


class InteractiveInput:
    """Local cbreak input; the remote canonical tty handles editing and echo."""
    def __init__(self, stream):
        self.fd = stream.fileno()
        self.saved = termios.tcgetattr(self.fd)
        try:
            tty.setcbreak(self.fd)
        except BaseException:
            self.close()
            raise

    def read(self):
        if not select.select([self.fd], [], [], 0)[0]:
            return None
        return os.read(self.fd, 256)

    def close(self):
        if self.saved is not None:
            termios.tcsetattr(self.fd, termios.TCSANOW, self.saved)
            self.saved = None


def check_image(path):
    data = path.read_bytes()
    image = LoadFirmwareImage('esp32c5', data)
    image.verify()
    if (image.checksum != image.calculate_checksum()
            or image.stored_digest != image.calc_digest):
        raise ValueError('Invalid image checksum/digest')
    if image.entrypoint != 0x40800000 or len(image.segments) != 2:
        raise ValueError('Unexpected SRAM image layout')
    for segment, low, high in zip(image.segments,
                                  (0x40800000, 0x40804000),
                                  (0x40804000, 0x40806000)):
        if not (segment.addr == low < segment.addr + len(segment.data) <= high):
            raise ValueError('Segment outside reserved SRAM')
    # Check the image and sources against the last successful build manifest.
    root = path.parent.parent
    for line in (path.parent / 'SHA256SUMS').read_text().splitlines():
        expected, name = line.split(maxsplit=1)
        actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f'Build manifest mismatch: {name}; rebuild first')
    print('Image:', path)
    print('Image SHA256:', hashlib.sha256(data).hexdigest())
    print('PASS: image checksum, digest, SRAM layout and build manifest')
    return data


def prepare_spi(esp):
    """Enable the ROM SPI interface and verify read-only known Flash ID.

    Deliberately do not use the broad attach_flash wrapper, whose optional
    vendor/status recovery actions would add variables to this comparison.
    """
    print('SPI_ATTACH: enable default ROM SPI interface (no Flash content write)')
    esp.flash_spi_attach(0)
    flash_id = esp.flash_id(cache=False) & 0xffffff
    print(f'ROM SPI preflight Flash ID: {flash_id:#08x}; expected 0x174020')
    if flash_id != 0x174020:
        raise RuntimeError('SPI preflight failed; SRAM image was not loaded')


CHECKPOINT=b'C5 SHELL CHECKPOINT: BusyBox hush PID1 returned; intentional halt at diagnostic exit'

def fnv(data):
    state=2166136261
    for byte in data: state=((state^byte)*16777619)&0xffffffff
    return state

def pad(data):
    return data+b'\0'*((-len(data))%64)

def validate_payloads(image, dt):
    if not 64<=len(image)<=0x200000 or image[56:60]!=b'RSC\x05':
        raise ValueError('Unexpected Linux Image header/size')
    if struct.unpack_from('<Q',image,8)[0]!=0:
        raise ValueError('M-mode Image must have zero load offset')
    span=struct.unpack_from('<Q',image,16)[0]
    if not len(image)<=span<0x7f0000:
        raise ValueError('Kernel memory span overlaps DT reserve')
    if not 40<=len(dt)<=0x10000 or dt[:4]!=b'\xd0\x0d\xfe\xed':
        raise ValueError('Unexpected DTB header/size')
    if struct.unpack_from('>I',dt,4)[0]!=len(dt):
        raise ValueError('DTB total size mismatch')
    return pad(image),pad(dt)

MEM_NAMES='result ram_start ram_end kernel_end dt_start kernel_reserved dt_nomap total_pages free_pages page_addr page_checks slab_checks irq_off'.split()
MEM_ROW=re.compile(rb'C5 MEM v1 '+b' '.join(name.encode()+rb'=([0-9a-f]{8})' for name in MEM_NAMES)+rb'\r?\n')

SHELL_NAMES='result pid entered calls returns seen errors exit_code enosys rx_bytes tx_bytes polls protection_restored irq_count callbacks acked jiffies_delta clock_faults source_off irq_off level_after'.split()
SHELL_ROW=re.compile(rb'C5 SHELL v1 '+b' '.join(name.encode()+rb'=([0-9a-f]{8})' for name in SHELL_NAMES)+rb'\r?\n')

class ShellProtocol:
    """Send one command only after its preceding prompt/result is observed."""
    def __init__(self, nonce, probe_missing=False, probe_exec=False, probe_repeat=False):
        if not re.fullmatch(rb'[0-9a-f]{16}',nonce):raise ValueError('Bad shell nonce')
        self.nonce=nonce;self.stage=0;self.cursor=0
        self.probe_missing=probe_missing
        self.probe_exec=probe_exec
        self.probe_repeat=probe_repeat
        self.commands=[b'n='+nonce+b'\n',
            b"v=$((6*7)); printf 'C5S:%s:%s:%s\\n' \"$n\" \"$v\" \"$$\"\n",
            b"for x in a b c; do printf '%s' \"$x\"; done; echo\n",
            b"printf 'C5END:%s\\n' \"$n\"\n",b'exit 0\n']
        if probe_missing:
            self.commands[-1:]=[b'c5_missing_command_probe\n',
                b"printf 'C5PROC:%s:returned\\n' \"$n\"\n",b'exit 0\n']
        if probe_exec:
            self.commands[-1:]=[b'/bin/cat /etc/c5-proof\n',b'/bin/ls /etc\n',
                b"printf 'C5EXEC:%s:returned\\n' \"$n\"\n",b'exit 0\n']
        if probe_repeat:
            self.commands[-1:]=[
                b"for r in 1 2 3; do /bin/cat /etc/c5-proof; /bin/ls /etc; printf 'C5ROUND:%s:%s\\n' \"$n\" \"$r\"; done\n",
                b"printf 'C5STRESS:%s:returned\\n' \"$n\"\n",b'exit 0\n']
    def next(self, raw):
        data=raw.replace(b'\r\n',b'\n')
        if self.stage>=len(self.commands):return None
        pos=self.cursor
        if self.stage==0:
            pos=data.find(b'C5 USER ENTER entry=',pos)
            if pos<0:return None
            pos=data.find(b'hush - the humble shell\n',pos)
        elif self.stage==2:pos=data.find(b'C5S:'+self.nonce+b':42:1\n',pos)
        elif self.stage==3:pos=data.find(b'\nabc\n',pos)
        elif self.stage==4:pos=data.find(b'C5END:'+self.nonce+b'\n',pos)
        if self.probe_missing and self.stage==6:
            pos=data.find(b'C5PROC:'+self.nonce+b':returned\n',pos)
        if self.probe_exec:
            if self.stage==5: pos=data.find(b'C5FILE:external-cat\n',pos)
            elif self.stage==6: pos=data.find(b'\nc5-proof\n',pos)
            elif self.stage==7: pos=data.find(b'C5EXEC:'+self.nonce+b':returned\n',pos)
        if self.probe_repeat:
            if self.stage==5: pos=data.find(b'C5ROUND:'+self.nonce+b':3\n',pos)
            elif self.stage==6: pos=data.find(b'C5STRESS:'+self.nonce+b':returned\n',pos)
        if pos<0:return None
        prompt=data.find(b'/ # ',pos)
        if prompt<0:return None
        self.cursor=prompt+4
        command=self.commands[self.stage];self.stage+=1
        return command

def assess(raw, nonce):
    data=raw.replace(b'\r\n',b'\n')
    if any(token in raw for token in [b'C5 LOAD FATAL',b'C5 LOAD TRAP',b'C5 MEM FAIL',b'C5 USER FAIL',b'C5 SHELL FAIL',b'Kernel panic',b'Oops:',b'BUG:',b'Warning: unable to open an initial console.']) or re.search(rb'(?:^|[\r\n])!',raw):
        return 1,'FAIL: loader/kernel/BusyBox shell checkpoint failure; no recovery claim'
    if b'C5 LOAD JUMP' in data and any(token in data[data.index(b'C5 LOAD JUMP'):] for token in (b'CPU_LOCKUP', b'ESP-ROM:esp32c5', b'waiting for download')):
        return 1,'FAIL: board reset during Linux shell session; no successful diagnostic exit/restoration claim'
    memory=list(MEM_ROW.finditer(data))
    for match in memory:
        row=dict(zip(MEM_NAMES,(int(v,16) for v in match.groups())))
        expected=dict(result=0,ram_start=0x42000000,ram_end=0x42800000,dt_start=0x427f0000,kernel_reserved=1,dt_nomap=1,page_checks=1024,slab_checks=64,irq_off=1)
        if (any(row[k]!=v for k,v in expected.items()) or not 0x42000000<row['kernel_end']<0x427f0000 or not 0<row['free_pages']<=row['total_pages']<=2032 or row['page_addr']%4096 or not row['kernel_end']<=row['page_addr']<=0x427ef000):
            return 1,'FAIL: memory ledger/reservations'
    shell=list(SHELL_ROW.finditer(data))
    for match in shell:
        row=dict(zip(SHELL_NAMES,(int(v,16) for v in match.groups())))
        expected=dict(result=0,pid=1,entered=1,errors=0,exit_code=0,protection_restored=1,clock_faults=0,source_off=1,irq_off=1,level_after=0)
        if (any(row[k]!=v for k,v in expected.items()) or row['seen']&0x49!=0x49 or not 10<=row['calls']<=4096 or row['returns']+1!=row['calls'] or row['enosys']>row['returns'] or not 1<=row['rx_bytes']<=65536 or not 1<=row['tx_bytes']<=262144 or not 1<=row['polls']<32768 or not 2<=row['irq_count']<32768 or any(row[k]!=row['irq_count'] for k in ['callbacks','acked','jiffies_delta'])):
            return 1,'FAIL: shell syscall/context/tty/clock ledger'
    if not re.fullmatch(rb'[0-9a-f]{16}',nonce):return 2,'INCONCLUSIVE: saved shell challenge missing'
    jump=data.find(b'C5 LOAD JUMP');banner=data.find(b'Linux version 6.12')
    enter=data.find(b'C5 USER ENTER entry=');busybox=data.find(b'BusyBox v1.37.0',enter)
    math=data.find(b'C5S:'+nonce+b':42:1\n',busybox)
    loop=data.find(b'\nabc\n',math);end=data.find(b'C5END:'+nonce+b'\n',loop)
    stop=data.find(CHECKPOINT+b'\n')
    if memory and shell and 0<=jump<banner<memory[-1].start()<enter<busybox<math<loop<end<shell[-1].start()<stop:
        return 0,'PASS (limited): real BusyBox hush PID1 executed USB-input variables/arithmetic/loop/printf builtins; diagnostic exit; external commands untested'
    return 2,'INCONCLUSIVE: incomplete ordered BusyBox shell command results/ledger/checkpoint'


def assess_repeat(raw,nonce):
    data=raw.replace(b'\r\n',b'\n')
    samples=re.findall(rb'C5 REAP n=(\d+) pid=(\d+) free=(\d+)\n',data)
    exec_pids=[int(x) for x in re.findall(rb'C5 USER EXEC pid=(\d+) ',data)]
    rounds=[b'C5ROUND:'+nonce+b':'+str(n).encode()+b'\n' for n in range(1,4)]
    if (len(samples)!=6 or [int(x[0]) for x in samples]!=list(range(1,7)) or
        len(exec_pids)!=6 or len(set(exec_pids))!=6 or
        sorted(exec_pids)!=sorted(int(x[1]) for x in samples) or
        data.count(b'C5FILE:external-cat\n')!=3 or
        data.count(b'\nc5-proof\n')!=3 or any(data.count(x)!=1 for x in rounds) or
        b'C5STRESS:'+nonce+b':returned\n' not in data):
        return 2,'INCONCLUSIVE: six exec/reap samples and three rounds required'
    steady=[int(x[2]) for x in samples[2:]]
    if min(steady)<=0 or max(steady)-min(steady)>4:
        return 1,'FAIL: free-page spread after warmup exceeds four-page diagnostic allowance'
    return 0,'PASS (limited): three cat/ls rounds, six children reaped; post-warmup free-page spread <=4; not long-term leak qualification'

def check_manifest(root, manifest):
    for line in manifest.read_text().splitlines():
        expected,name=line.split(maxsplit=1)
        if hashlib.sha256((root/name).read_bytes()).hexdigest()!=expected:
            raise ValueError('Manifest mismatch: '+name)

def execute(args, image_data, kernel, dt, raw_path):
    print('Board action: ROM reset, SRAM loader, USB transfer and M-mode Kernel jump.')
    print('PSRAM action: overwrite kernel range at 0x42000000 and DT at 0x427f0000; NO original-data restoration.')
    print('PMA adds W/X; cache/MMU and CLIC configuration remain changed at intentional Kernel halt.')
    print('Shell test: real BusyBox hush PID1; automatic USB-input builtin commands then diagnostic exit.')
    print('User protection: temporary HP_APM REE0 RWX/PMP8MiB; own changes restored only at diagnostic exit.')
    print('No Flash/eFuse write/erase; no explicit post-capture reset or recovery.')
    nonce=secrets.token_hex(8).encode()
    raw_path.with_suffix('.challenge.txt').write_bytes(nonce+b'\n')
    protocol=ShellProtocol(nonce,getattr(args,"probe_missing",False),getattr(args,"probe_exec",False),getattr(args,"probe_repeat",False))
    print('Shell challenge:',nonce.decode())
    interactive=getattr(args, "interactive", False)
    manual_seconds=getattr(args,"manual_seconds",120)
    terminal=None
    manual_deadline=None
    warned=False
    finishing=False
    raw=bytearray()
    port=serial.Serial(port=None,baudrate=115200,timeout=.1,write_timeout=5,exclusive=True)
    port.dtr=False;port.rts=False;port.port=args.port
    with raw_path.open('xb') as log:
        def record(data, show=True):
            raw.extend(data);log.write(data);log.flush()
            if show: print(data.decode('ascii',errors='backslashreplace'),end='',flush=True)
        def wait_line(token, timeout=30, show=True):
            deadline=time.monotonic()+timeout
            pending=bytearray()
            while time.monotonic()<deadline:
                data=port.read_until(b'\n',4096)
                if not data: continue
                record(data,show);pending.extend(data)
                if b'C5 LOAD FATAL' in pending or b'C5 LOAD TRAP' in pending:
                    raise RuntimeError(pending.decode('ascii',errors='replace'))
                if token+b'\r\n' in pending or token+b'\n' in pending:return
                if len(pending)>65536:raise RuntimeError('Unexpected loader output')
            raise TimeoutError('Waiting for '+token.decode())
        try:
            port.open();esp=ESP32C5ROM(port);esp.connect(mode='default-reset',attempts=3)
            prepare_spi(esp);load_ram(esp,image_data);port.timeout=.1
            wait_line(b'C5 LOAD READY v1')
            port.write(struct.pack('<6I',0x354c444b,1,len(kernel),len(dt),fnv(kernel),fnv(dt))+bytes(40))
            wait_line(b'C5 LOAD BEGIN')
            seq=0
            for name,data in [('kernel',kernel),('dt',dt)]:
                for pos in range(0,len(data),64):
                    chunk=data[pos:pos+64]
                    packet=struct.pack('<3I',seq,fnv(chunk),64)+chunk
                    if port.write(packet)!=len(packet):raise RuntimeError('Short USB write')
                    wait_line(f'C5 LOAD ACK {seq:08x}'.encode(),show=False)
                    seq+=1
                    if (pos+64)%65536==0 or pos+64==len(data):
                        print(f'Transfer verified by SPI: {name} {pos+64}/{len(data)} bytes')
            wait_line(b'C5 LOAD READY-JUMP entry=42000000 dt=427f0000')
            port.write(b'GO!!')
            deadline=time.monotonic()+((manual_seconds+25) if interactive else (35 if getattr(args,"probe_repeat",False) else 30))
            while time.monotonic()<deadline:
                data=port.read(min(max(port.in_waiting,1),4096))
                if data:record(data)
                command=protocol.next(raw)
                if interactive and command == b'exit 0\n':
                    terminal=InteractiveInput(sys.stdin)
                    manual_deadline=min(time.monotonic()+manual_seconds, deadline-5)
                    print(f'\nMANUAL: up to {manual_seconds} seconds; cat/ls and shell builtins; no pipelines/jobs qualified.')
                    print('Try: echo hello   or   printf "%s\\n" "$((6*7))"')
                    print('Finish with exit 0 or Ctrl-]; timer requests exit automatically.')
                    print('Kernel diagnostic limits: 32768 ticks / 4096 PID1 syscalls; syscall count is not monitored live.')
                    command=None
                if terminal is not None and not finishing:
                    now=time.monotonic()
                    typed=terminal.read()
                    if not warned and now >= manual_deadline-5:
                        print('\nMANUAL: five seconds or less remain; finish the current command.')
                        warned=True
                    if now >= manual_deadline or typed == b'' or (typed is not None and b'\x1d' in typed):
                        command=b'\x15exit 0\n'
                        finishing=True
                        print('\nHost requesting diagnostic exit (clears pending input line).')
                    elif typed:
                        command=typed
                if command is not None:
                    if port.write(command)!=len(command):raise RuntimeError('Short shell command write')
                    if terminal is None: print('Host sent shell command',protocol.stage)
                code,_=assess(raw,nonce)
                if code!=2:break
        finally:
            try:
                if terminal is not None: terminal.close()
            finally:
                port.close()
    code,msg=assess(raw,nonce)
    if getattr(args,'probe_missing',False) and code==0:
        if b'C5PROC:'+nonce+b':returned\r\n' not in raw and b'C5PROC:'+nonce+b':returned\n' not in raw:
            code,msg=2,'INCONCLUSIVE: missing-command continuation marker absent'
        elif b"can't execute 'c5_missing_command_probe': No such file or directory" not in raw:
            code,msg=2,'INCONCLUSIVE: expected missing-command error absent'
        else:
            msg='PASS (limited): missing external command returned to shell and diagnostic exit; successful external exec still untested'
    if getattr(args,'probe_exec',False) and code==0:
        normalized=raw.replace(b'\r\n',b'\n')
        if normalized.count(b'C5 USER EXEC pid=')<2 or any(x not in normalized for x in
            [b'C5FILE:external-cat\n',b'\nc5-proof\n',b'C5EXEC:'+nonce+b':returned\n']):
            code,msg=2,'INCONCLUSIVE: external exec/output/continuation evidence missing'
        else:
            msg='PASS (limited): external BusyBox cat/ls exec, output and shell continuation; diagnostic exit'
    if getattr(args,'probe_repeat',False) and code==0:
        code,msg=assess_repeat(raw,nonce)
    print('\n'+msg)
    print('Port closed; board state/recovery after close not verified. No restoration attempted.')
    return code

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',default='/dev/ttyACM0');parser.add_argument('--check-only',action='store_true')
    parser.add_argument('--interactive',action='store_true',help='After automatic preflight, hand keyboard input to the board shell')
    parser.add_argument('--probe-missing',action='store_true',help='Automatic diagnostic: missing external command, return marker, exit')
    parser.add_argument('--probe-exec',action='store_true',help='Automatic external cat/ls and shell continuation test')
    parser.add_argument('--probe-repeat',action='store_true',help='Three cat/ls rounds and bounded post-reap free-page samples')
    parser.add_argument('--manual-seconds',type=int,default=120,help='Interactive window in seconds, 15..240 (default120)')
    args=parser.parse_args()
    if not 15<=args.manual_seconds<=240: parser.error('--manual-seconds must be 15..240')
    if sum([args.interactive,args.probe_missing,args.probe_exec,args.probe_repeat])>1: parser.error('Choose one test mode')
    if args.probe_missing and args.interactive: parser.error('Choose either --probe-missing or --interactive')
    if args.interactive and not args.check_only and not sys.stdin.isatty():
        parser.error('--interactive requires a terminal on standard input')
    if esptool.__version__!='5.6.0':parser.error('Requires reviewed esptool 5.6.0')
    root=Path(__file__).resolve().parent;project=root.parent.parent
    config=(project/'kernel/build/.config').read_text()
    if not args.check_only and 'root=mtd:rootfs' in config:
        parser.error('This kernel requires the independent Flash rootfs. The legacy RAM uploader does not transfer it; use bootloader/capture.py after verified Flash installation.')
    logs=project/'logs';logs.mkdir(exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+f'-{time.time_ns()}'
    transcript=logs/f'linux-shell-{stamp}.txt';raw_path=logs/f'linux-shell-{stamp}.serial.bin'
    print('Transcript:',transcript)
    with transcript.open('x') as output,contextlib.redirect_stdout(Tee(sys.stdout,output)),contextlib.redirect_stderr(Tee(sys.stderr,output)):
        try:
            print('UTC:',datetime.now(timezone.utc).isoformat())
            data=check_image(root/'build/linux-shell.bin')
            kernel=(root/'build/Image').read_bytes();dt=(root/'build/c5-early.dtb').read_bytes()
            print('Kernel SHA256:',hashlib.sha256(kernel).hexdigest())
            print('DT SHA256:',hashlib.sha256(dt).hexdigest())
            kernel,dt=validate_payloads(kernel,dt)
            print(f'Kernel padded: {len(kernel)} bytes; DT padded: {len(dt)} bytes')
            if args.check_only: print('CHECK ONLY: no device opened');return 0
            return execute(args,data,kernel,dt,raw_path)
        except KeyboardInterrupt:
            print('Interrupted; PSRAM/configuration not restored; no reset/retry.');return 130
        except Exception as error:
            print(f'ERROR: {type(error).__name__}: {error}')
            print('No reset/retry/recovery; share transcript. PSRAM/configuration may remain changed.');return 1

if __name__=='__main__':sys.exit(main())
