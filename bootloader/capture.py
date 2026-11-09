#!/usr/bin/env python3
"""Passive USB capture after owner-triggered normal reset; no reset/loader/write commands."""
import argparse,re,sys,time,json,errno,runpy,secrets,termios
from serial.tools import list_ports
from pathlib import Path
import serial
ROOT=Path(__file__).resolve().parent

class QuietSerial(serial.Serial):
    """Keep initial boot capture unchanged; opt into quieter close/reopen.

    The OS/device can still generate control changes. This cannot guarantee
    absence of a reset; fresh nonce/no-new-boot evidence is still required.
    """
    quiet_mode=False
    def _update_dtr_state(self):
        if not self.quiet_mode:super()._update_dtr_state()
    def _update_rts_state(self):
        if not self.quiet_mode:super()._update_rts_state()
    def _clear_hupcl(self):
        attrs=termios.tcgetattr(self.fd)
        if attrs[2] & termios.HUPCL:
            attrs[2] &= ~termios.HUPCL
            termios.tcsetattr(self.fd,termios.TCSANOW,attrs)
        self.hupcl_disabled=not bool(termios.tcgetattr(self.fd)[2] & termios.HUPCL)
    def enable_quiet(self):
        self._clear_hupcl()
        self.quiet_mode=True
        print('HOST REOPEN POLICY: HUPCL cleared; subsequent opens skip explicit DTR/RTS writes.',flush=True)
    def _reconfigure_port(self,force_update=False):
        super()._reconfigure_port(force_update)
        if self.quiet_mode:self._clear_hupcl()

def assess(raw):
    if b'C5 INIT FAIL' in raw:
        return 1,'FAIL: init mount/shell startup failed'
    if b'TG0_WDT_HPSYS' in raw:
        return 1,'FAIL: TG0 watchdog reset observed; stop capture, no successful boot/session claim'
    if any(x in raw for x in [b'C5 LOAD FATAL',b'C5 LOAD TRAP',b'C5 FLASH SRAM FATAL',b'CPU_LOCKUP',b'Kernel panic',b'Oops:']):
        return 1,'FAIL: boot/trap/reset/panic observed'
    cursor=0
    for marker in [b'C5 FLASH BOOT v1',b'C5 FLASH VERIFIED; JUMP entry=42000000 dt=427f0000',
        b'Linux version 6.12',b'hush - the humble shell',b'/ # ']:
        position=raw.find(marker,cursor)
        if position<0:return 2,'INCONCLUSIVE: full Flash-loader/Linux/shell sequence not captured'
        cursor=position+len(marker)
    return 0,'PASS (limited): Flash-loader verified payload and reached Linux BusyBox prompt after owner reset; full power-cycle/unattended operation untested'

def session_tools():
    # Import helpers only; run_path does not invoke the RAM uploader's main().
    return runpy.run_path(str(ROOT.parent/'bootloader/psram/run.py'))

def assess_interactive(raw,helpers):
    boot_code,_=assess(raw)
    if boot_code!=0:return boot_code,'FAIL/INCONCLUSIVE: Flash boot sequence unavailable'
    # Reuse the qualified memory/syscall/clock ledger checks. With no saved
    # nonce, that assessor cannot claim its separate automated command proof.
    code,msg=helpers['assess'](raw,b'')
    if code==1:return code,msg
    rows=list(helpers['SHELL_ROW'].finditer(raw))
    if not helpers['MEM_ROW'].search(raw) or not rows or raw.find(helpers['CHECKPOINT'],rows[-1].end())<0:
        return 2,'INCONCLUSIVE: Flash shell diagnostic exit ledger/checkpoint not complete'
    return 0,'PASS (limited): Flash-boot shell keyboard session reached clean diagnostic exit; external commands qualified only if observed in this session'

def interact(port,raw,record,txlog,seconds,hard_end,helpers,terminal_factory=None,clock=None):
    clock=clock or time.monotonic
    terminal_factory=terminal_factory or helpers['InteractiveInput']
    terminal=terminal_factory(sys.stdin)
    try:
        end=min(clock()+seconds,hard_end-10);warned=False;finishing=False
        print(f'\nMANUAL: up to {max(0,int(end-clock()))} seconds. Try /bin/ls /etc or /bin/cat /etc/c5-proof.')
        print('Finish with exit 0 or Ctrl-]. Timeout requests exit; Ctrl-C stops host capture.')
        print('Kernel remains bounded: 32768 ticks /4096 PID1 calls; no live syscall-budget display.')
        while clock()<hard_end:
            data=port.read(min(max(port.in_waiting,1),4096))
            if data:record(data)
            code,msg=assess_interactive(raw,helpers)
            if code!=2:return code,msg
            if not finishing:
                now=clock();typed=terminal.read()
                if not warned and now>=end-5:
                    print('\nMANUAL: five seconds or less remain; finish the command.',flush=True);warned=True
                if now>=end or typed==b'' or (typed is not None and b'\x1d' in typed):
                    typed=b'\x15exit 0\n';finishing=True
                    print('\nHost requesting normal diagnostic exit.',flush=True)
                if typed:
                    txlog.write(typed);txlog.flush()
                    if port.write(typed)!=len(typed):raise RuntimeError('Short console input write')
        return assess_interactive(raw,helpers)
    finally:terminal.close()

def reconnect_session(port,raw,record,txlog,helpers,clock=None,pause=None,nonce=None):
    """Fresh challenge before/after one host close/open; no reset or ROM calls."""
    clock=clock or time.monotonic;pause=pause or time.sleep
    nonce=nonce or secrets.token_hex(8).encode()
    if not re.fullmatch(rb'[0-9a-f]{16}',nonce):raise ValueError('Invalid reconnect nonce')
    policy=re.search(rb'C5 USB RESET POLICY before=[0-9a-f]{8} after=([0-9a-f]{8}) serial_reset_disabled=1',raw)
    if not policy or not (int(policy[1],16)&4):
        return 2,'INCONCLUSIVE: reset-policy readback marker missing; reconnect not attempted'
    boot_count=raw.count(b'C5 FLASH BOOT v1');linux_count=raw.count(b'Linux version 6.12')
    def send(command):
        txlog.write(command);txlog.flush()
        if port.write(command)!=len(command):raise RuntimeError('Short reconnect-test command write')
    def await_response(start,marker,prompt=True):
        end=clock()+15
        while clock()<end:
            data=port.read(min(max(port.in_waiting,1),4096))
            if data:record(data)
            if assess(raw)[0]==1:raise RuntimeError('Reset/trap/panic during reconnect test')
            if raw.count(b'C5 FLASH BOOT v1')!=boot_count or raw.count(b'Linux version 6.12')!=linux_count:
                raise RuntimeError('A new boot occurred; reconnect did not preserve the running shell')
            pos=raw.find(marker,start)
            if pos>=0 and (not prompt or raw.find(b'/ # ',pos+len(marker))>=0):return
        raise TimeoutError('Fresh shell response missing after host reconnect')
    def challenge(stage):
        start=len(raw)
        send(b"printf 'C5LINK:"+nonce+b':'+stage+b":%s\\n' \"$$\"\n")
        await_response(start,b'C5LINK:'+nonce+b':'+stage+b':1\r\n')
    challenge(b'before')
    print('RECONNECT TEST: closing then reopening the same serial port; no reset command.',flush=True)
    if hasattr(port,'enable_quiet'):port.enable_quiet()
    port.close();pause(.5);open_after_reconnect(port,clock,pause)
    challenge(b'after')
    start=len(raw);send(b'/bin/cat /etc/c5-proof\n')
    await_response(start,b'C5FILE:external-cat\r\n')
    start=len(raw);send(b'exit 0\n')
    await_response(start,helpers['CHECKPOINT'],prompt=False)
    code,msg=assess_interactive(raw,helpers)
    if code==0:
        return 0,'PASS (limited): fresh PID1 replies before/after serial close/open, external cat and clean exit; actual warm system reset remains unqualified'
    return code,msg

def init_session(port,raw,record,txlog,helpers,clock=None,nonce=None,flash_read=False,expected_data_fnv=None):
    """Validate actual uname/proc/mount output after /init, then exit."""
    clock=clock or time.monotonic;nonce=nonce or secrets.token_hex(8).encode()
    if not re.fullmatch(rb'[0-9a-f]{16}',nonce):raise ValueError('Invalid init nonce')
    ready=raw.find(b'C5 INIT READY: proc and sysfs mounted; starting interactive shell')
    if ready<0 or raw.find(b'hush - the humble shell',ready)<0:
        return 2,'INCONCLUSIVE: /init mount-ready marker absent'
    if b'C5 ROOTFS READY: independent cramfs mounted read-only;' not in raw[:ready]:
        return 2,'INCONCLUSIVE: independent rootfs marker absent; old initramfs is not qualified'
    if flash_read:
        probe=re.search(rb'C5 FLASH READY id=174020 offset=00400000 bytes=00400000 write=0 sample_fnv=([0-9a-f]{8})',raw[:ready])
        if not probe or int(probe[1],16)!=expected_data_fnv:
            return 1,'FAIL: read-only SRAM Flash probe or expected sample hash absent/mismatched'
    boots=raw.count(b'C5 FLASH BOOT v1')
    def exchange(command,patterns,prompt=True):
        start=len(raw);txlog.write(command);txlog.flush()
        if port.write(command)!=len(command):raise RuntimeError('Short init-test command write')
        end=clock()+15
        while clock()<end:
            data=port.read(min(max(port.in_waiting,1),4096))
            if data:record(data)
            if assess(raw)[0]==1:raise RuntimeError('Boot/trap/init failure during user test')
            if raw.count(b'C5 FLASH BOOT v1')!=boots:raise RuntimeError('Unexpected new boot during init test')
            data=bytes(raw[start:]);matches=[re.search(pattern,data) for pattern in patterns]
            if all(matches) and (not prompt or data.find(b'/ # ',max(m.end() for m in matches))>=0):return data
        raise TimeoutError('Expected init/user command output absent')
    exchange(b'/bin/uname -a\n',[rb'(?:^|[\r\n])Linux [^\r\n]*riscv32[^\r\n]*\r?\n'])
    exchange(b'/bin/cat /proc/cpuinfo\n',[rb'processor\s*:\s*0',rb'isa\s*:\s*rv32'])
    exchange(b'/bin/cat /proc/mounts\n',[rb'proc /proc proc ',rb'sysfs /sys sysfs ',
                                             rb'mtd:rootfs / cramfs ro(?:,| )'])
    if flash_read:
        output=exchange(b'/bin/cat /proc/mtd\n',[rb'mtd[0-9]+: 00400000 00001000 "data"'])
        number=re.search(rb'mtd([0-9]+): 00400000 00001000 "data"',output)[1]
        exchange(b'/bin/dd if=/dev/mtd'+number+b" of=/tmp/c5-flash-sample bs=64 count=1 && echo C5READ:OK\n",[rb'C5READ:OK\r?\n'])
        output=exchange(b'/bin/hexdump -v -e \'1/1 "%02x"\' /tmp/c5-flash-sample; echo\n',[rb'[0-9a-f]{128}\r?\n'])
        sample=bytes.fromhex(re.search(rb'([0-9a-f]{128})\r?\n',output)[1].decode())
        value=2166136261
        for byte in sample:value=((value^byte)*16777619)&0xffffffff
        if value!=expected_data_fnv:raise RuntimeError('MTD user read differs from saved backup sample')
    exchange(b"printf 'C5INIT:"+nonce+b":%s\\n' \"$$\"\n",[b'C5INIT:'+nonce+rb':1\r?\n'])
    exchange(b'exit 0\n',[re.escape(helpers['CHECKPOINT'])],prompt=False)
    code,msg=assess_interactive(raw,helpers)
    if code==0:return 0,('PASS (limited): SRAM Flash ID/sample and actual /dev/mtd read matched backup; clean shell exit; writes remain disabled' if flash_read else 'PASS (limited): independent cramfs root mounted read-only; uname, proc/sysfs, PID1 continuation and clean exit; persistence unqualified, time limit remains')
    return code,msg

class CycleGate:
    """Ignore old-session data; require observed USB absence for >=5 seconds."""
    def __init__(self): self.gone_at=None;self.offline_seconds=None
    def update(self,present,now):
        if self.gone_at is None:
            if not present:self.gone_at=now
            return False
        if not present:return False
        self.offline_seconds=now-self.gone_at
        if self.offline_seconds<5:raise ValueError('Reconnect was too soon; keep all board power disconnected for at least5 seconds')
        return True

def usb_identity(port):
    if port.serial_number:
        return (port.vid,port.pid,'serial',port.serial_number)
    if port.location:
        return (port.vid,port.pid,'location',port.location)
    return None

def wait_power_cycle(path):
    records=list(list_ports.comports())
    before=next((p for p in records if Path(p.device).resolve()==Path(path).resolve()),None)
    if before is None:raise ValueError('Start --power-cycle with the board plugged in and its USB port present')
    identity=usb_identity(before)
    if identity is None:raise ValueError('USB serial/location identity unavailable')
    gate=CycleGate();deadline=time.monotonic()+180
    print('WAIT: unplug USB AND all other board power for at least5 seconds, then reconnect USB. Do not press BOOT.',flush=True)
    announced=False
    while time.monotonic()<deadline:
        matches=[p for p in list_ports.comports() if usb_identity(p)==identity]
        if len(matches)>1:raise ValueError('Ambiguous USB identity')
        ready=gate.update(bool(matches),time.monotonic())
        if gate.gone_at is not None and not announced:
            print('USB DISCONNECTED: keep all power removed for at least5 seconds.',flush=True);announced=True
        if ready:
            print(f'USB RECONNECTED: {matches[0].device}, absent {gate.offline_seconds:.1f}s',flush=True)
            return matches[0].device,dict(usb_identity=identity,usb_absent_seconds=gate.offline_seconds,
                power_removal_independently_verified=False)
        time.sleep(.2)
    raise TimeoutError('No complete USB disconnect/reconnect within180 seconds')

def open_after_reconnect(port,clock=None,pause=None):
    """Bounded grace for device-node/udev readiness; never change permissions."""
    clock=clock or time.monotonic;pause=pause or time.sleep
    start=clock();attempts=0
    while True:
        attempts+=1
        try:
            port.open()
            return dict(open_attempts=attempts,open_wait_seconds=clock()-start)
        except (serial.SerialException,OSError) as error:
            port.close()
            if getattr(error,'errno',None) not in (errno.EACCES,errno.ENOENT,errno.ENODEV):raise
            if clock()-start>=10:
                raise RuntimeError('USB port still inaccessible after10 seconds; preserve this error and check host serial permissions/device identity; no permission changes made') from error
            if attempts==1:print('WAIT: USB node appeared but is not ready; retrying access for up to10 seconds.',flush=True)
            pause(.2)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',default='/dev/ttyACM0');p.add_argument('--seconds',type=int,default=60)
    p.add_argument('--power-cycle',action='store_true',help='Wait for same USB device to disappear for5 seconds and reconnect before capture')
    p.add_argument('--interactive',action='store_true',help='Hand keyboard to the Flash-booted shell after prompt')
    p.add_argument('--manual-seconds',type=int,default=120,help='Interactive window15..240 seconds (default120)')
    p.add_argument('--reconnect-test',action='store_true',help='Automatic same-session serial close/open test after power-cycle boot')
    p.add_argument('--quiet-reopen',action='store_true',help='Reconnect test: clear HUPCL before close and skip explicit modem writes on reopen')
    p.add_argument('--init-test',action='store_true',help='Automatic /init/uname/proc/sysfs user test after power-cycle boot')
    p.add_argument('--flash-read-test',action='store_true',help='Also validate read-only SRAM Flash and actual user MTD read')
    p.add_argument('--data-sample-fnv',type=lambda value:int(value,16),help='Expected FNV-1a of first64 bytes of data region from owner backup')
    args=p.parse_args()
    if args.init_test and (args.interactive or args.reconnect_test or not args.power_cycle):p.error('--init-test requires --power-cycle and excludes other session modes')
    if args.flash_read_test and (not args.init_test or args.data_sample_fnv is None):p.error('--flash-read-test requires --init-test and --data-sample-fnv')
    if args.quiet_reopen and not args.reconnect_test:p.error('--quiet-reopen requires --reconnect-test')
    if args.quiet_reopen and serial.__version__!='3.5':p.error('--quiet-reopen requires reviewed pyserial3.5')
    if args.reconnect_test and (args.interactive or not args.power_cycle):p.error('--reconnect-test requires --power-cycle and excludes --interactive')
    if not 15<=args.manual_seconds<=240:p.error('--manual-seconds must be15..240')
    if args.interactive and not sys.stdin.isatty():p.error('--interactive requires a terminal on standard input')
    if not 15<=args.seconds<=180:p.error('--seconds must be15..180')
    path=ROOT.parent/'logs'/f'flash-boot-capture-{time.time_ns()}.serial.bin'
    event=None
    if args.power_cycle:
        args.port,event=wait_power_cycle(args.port)
        path.with_suffix('.event.json').write_text(json.dumps(event,indent=2)+'\n')
    port_class=QuietSerial if args.quiet_reopen else serial.Serial
    port=port_class(port=None,baudrate=115200,timeout=.2,exclusive=True)
    port.dtr=False;port.rts=False;port.port=args.port
    raw=bytearray();session_result=None
    print('Raw log:',path)
    print('Capture after USB reconnect; no button needed.' if args.power_cycle else 'Passive capture: after it opens, press RESET/EN with BOOT released. No host upload or Flash write.')
    try:
        if args.power_cycle:
            try:
                event.update(open_after_reconnect(port))
            except Exception as error:
                event['open_error']=str(error)
                path.with_suffix('.event.json').write_text(json.dumps(event,indent=2)+'\n')
                raise
            path.with_suffix('.event.json').write_text(json.dumps(event,indent=2)+'\n')
        else:port.open()
        print('OPEN: capturing reconnect boot; do not press buttons.' if args.power_cycle else 'OPEN: press RESET/EN now; leave BOOT released.',flush=True)
        opened_at=time.monotonic()
        end=opened_at+args.seconds
        with path.open('xb') as log:
            def record(data):
                raw.extend(data);log.write(data);log.flush()
                print(data.decode('ascii',errors='backslashreplace'),end='',flush=True)
            while time.monotonic()<end:
                data=port.read(min(max(port.in_waiting,1),4096))
                if data:
                    record(data)
                code,message=assess(raw)
                if args.init_test and code==0:
                    with path.with_suffix('.stdin.bin').open('xb') as txlog:
                        nonce=secrets.token_hex(8).encode()
                        path.with_suffix('.challenge.txt').write_bytes(nonce+b'\n')
                        try:session_result=init_session(port,raw,record,txlog,session_tools(),nonce=nonce,
                            flash_read=args.flash_read_test,expected_data_fnv=args.data_sample_fnv)
                        except (RuntimeError,TimeoutError) as error:session_result=(1,'FAIL: init/user test: '+str(error))
                    break
                if args.reconnect_test and code==0:
                    with path.with_suffix('.stdin.bin').open('xb') as txlog:
                        nonce=secrets.token_hex(8).encode()
                        path.with_suffix('.challenge.txt').write_bytes(nonce+b'\n')
                        try:
                            session_result=reconnect_session(port,raw,record,txlog,session_tools(),nonce=nonce)
                        except (RuntimeError,TimeoutError) as error:
                            session_result=(1,'FAIL: serial reconnect test: '+str(error))
                        if event is not None:
                            event.update(quiet_reopen_requested=args.quiet_reopen,quiet_reopen_active=getattr(port,'quiet_mode',False),hupcl_disabled=getattr(port,'hupcl_disabled',False),session_result_code=session_result[0])
                            path.with_suffix('.event.json').write_text(json.dumps(event,indent=2)+'\n')
                    break
                if args.interactive and code==0:
                    with path.with_suffix('.stdin.bin').open('xb') as txlog:
                        session_result=interact(port,raw,record,txlog,args.manual_seconds,opened_at+300,session_tools())
                    break
                if code!=2:break
    finally:port.close()
    code,message=session_result if session_result is not None else assess(raw)
    if args.power_cycle and code==0 and not (args.interactive or args.reconnect_test or args.init_test):
        reason=re.search(rb'rst:[^\r\n]+',raw)
        observed=reason[0].decode('ascii',errors='replace') if reason else 'not captured'
        message='PASS (limited): Flash/Linux/hush boot after observed USB disconnect/reconnect; reset cause: '+observed
        print('All-power removal follows owner procedure; USB-open reset may mask the initial power-on cause.')
    print('\n'+message)
    print('Port closed; no reset/recovery/continued-execution claim.')
    return code
if __name__=='__main__':
    try:sys.exit(main())
    except (Exception,KeyboardInterrupt) as error:
        print(f'Capture stopped: {type(error).__name__}: {error}',file=sys.stderr)
        print('No kernel upload, Flash write or recovery attempted; console input may have been sent in interactive mode.',file=sys.stderr);sys.exit(2)
