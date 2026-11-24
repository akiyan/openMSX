#!/usr/bin/env python3
"""Check that openMSX survives loadstate and reverse goback while it plays
and records a PSG tone.

Runs openMSX unthrottled on C-BIOS MSX2 (no ROM needed): savestate, start a
recording, loadstate (emulated time goes back), reverse goback, then stop
the recording. openMSX must exit normally at the end of the script.

The first version of the record fix (akiyan/openMSX#2) generated the sound
up to the current time in MSXMixer::reInit(), which also runs right after
a savestate's scheduler is loaded into a new machine. Its mixer clock was
still at 0, so it tried to generate seconds of sound into a buffer of 8192
samples and crashed (akiyan/openMSX#3).

    make install INSTALL_BASE=$PWD/derived/install
    python3 tests/akiyan/record_loadstate.py [--binary derived/install/bin/openmsx]
"""

import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile


def tcl_script(work):
    return '\n'.join([
        'set renderer none',
        'set throttle off',
        'reverse start',
        'proc psg {r v} { debug write ioports 0xA0 $r; debug write ioports 0xA1 $v }',
        'after time 0.5 { psg 0 112; psg 1 0; psg 7 0x3E; psg 8 15 }',
        f'set log [open {{{work}/marks.txt}} w]',
        'proc mark {what} { puts $::log "$what [machine_info time]"; flush $::log }',
        'after time 1.0 { mark save; savestate s1 }',
        f'after time 1.5 {{ record start -audioonly -mono {{{work}/rec.wav}} }}',
        # loadstate goes back to 1.0; the later steps are relative to it
        'after time 3.0 { mark load; loadstate s1; mark loaded'
        ' ; after time 2.0 { mark goback; reverse goback 0.5; mark wentback'
        ' ; after time 2.5 { record stop; mark end; close $::log; exit } } }',
    ]) + '\n'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--binary')
    args = ap.parse_args()
    binary = args.binary or (sorted(glob.glob('derived/install/bin/openmsx')) or [None])[0]
    if not binary:
        sys.exit('openMSX not found; run make install INSTALL_BASE=$PWD/derived/install or pass --binary')
    binary = os.path.abspath(binary)

    work = tempfile.mkdtemp(prefix='record-loadstate-')
    home = os.path.join(work, 'home')
    os.mkdir(home)
    script = os.path.join(work, 'run.tcl')
    open(script, 'w').write(tcl_script(work))
    cmd = [binary, '-machine', 'C-BIOS_MSX2', '-script', script]
    if not os.environ.get('DISPLAY') and shutil.which('xvfb-run'):
        cmd = ['xvfb-run', '-a'] + cmd
    env = dict(os.environ, OPENMSX_HOME=home)
    share = os.path.join(os.path.dirname(os.path.dirname(binary)), 'share')
    if 'OPENMSX_SYSTEM_DATA' not in env and os.path.isdir(os.path.join(share, 'machines')):
        env['OPENMSX_SYSTEM_DATA'] = share
    r = subprocess.run(cmd, env=env, timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    marks_file = os.path.join(work, 'marks.txt')
    marks = [line.split()[0] for line in open(marks_file)] if os.path.exists(marks_file) else []
    print(f'exit {r.returncode}, reached: {" ".join(marks)}')
    if r.returncode != 0 or marks[-1:] != ['end']:
        sys.exit(f'FAIL: openMSX did not reach the end of the script ({work})')
    shutil.rmtree(work)
    print('OK')


if __name__ == '__main__':
    main()
