#!/usr/bin/env python3
"""Check that `record` keeps every sample of the emulated time it records.

Runs openMSX unthrottled on C-BIOS MSX2 (no ROM needed) with a PSG tone and
records its sound (`record start -audioonly`):

- one recording over which the emulation pauses PAUSES times
  (`set pause on`, then off again after some wall-clock time), and
- PIECES recordings one after another, each stopped and started again.

Each WAV must hold the emulated time from its start to its stop, to a
sample, and the paused one's tone must go on through the pauses. openMSX 22.0
dropped the samples from the mixer's last update to the moment whenever it
reset its clock (the pause's end, a recording's start and stop): up to a
buffer (11.6 ms at 44.1 kHz) at each, so the sound of a long recording with
pauses fell ever earlier than its emulated time. It also made the sound
devices new resamplers there, which jolted the tone by up to 12 samples.

    make install INSTALL_BASE=$PWD/derived/install
    python3 tests/akiyan/record_pause.py [--binary derived/install/bin/openmsx]
"""

import argparse
import array
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave

START = 1.0       # emulated seconds after boot of the first recording's start
SPAN = 3.0        # emulated seconds of the paused recording
PAUSES = 20
PIECES = 10
PIECE = 0.25      # emulated seconds of each piece


def tcl_script(work):
    lines = [
        'set renderer none',
        'set throttle off',
        'proc psg {r v} { debug write ioports 0xA0 $r; debug write ioports 0xA1 $v }',
        # channel A: a 1 kHz tone at full volume, B and C off
        'after time 0.5 { psg 0 112; psg 1 0; psg 7 0x3E; psg 8 15 }',
        f'set log [open {{{work}/times.txt}} w]',
        'proc mark {what} { puts $::log "$what [machine_info time]"; flush $::log }',
        f'after time {START} {{ mark start; record start -audioonly -mono {{{work}/paused.wav}} }}',
    ]
    for k in range(PAUSES):
        # not on a multiple of the mixer's buffer
        at = START + (k + 0.5) * SPAN / PAUSES + 0.0013 * k
        lines.append(f'after time {at:.6f} {{ set pause on; after realtime 0.02 {{ set pause off }} }}')
    stop = START + SPAN
    lines.append(f'after time {stop:.6f} {{ record stop; mark stop }}')
    for k in range(PIECES):
        at = stop + 0.1 + k * PIECE
        lines.append(f'after time {at:.6f} {{ mark piece{k}; '
                     f'record start -audioonly -mono {{{work}/piece{k}.wav}} }}')
        lines.append(f'after time {at + PIECE - 0.0001:.6f} {{ record stop; mark end{k} }}')
    lines.append(f'after time {stop + 0.2 + PIECES * PIECE:.6f} {{ close $::log; exit }}')
    return '\n'.join(lines) + '\n'


def frames(path):
    with wave.open(path) as w:
        return w.getnframes(), w.getframerate()


def jolts(path):
    """(sample, spacing) of the tone's rising edges whose spacing is off its
    usual one by more than a sample."""
    with wave.open(path) as w:
        x = array.array('h', w.readframes(w.getnframes()))
    mean = sum(x) / len(x)
    edges = [i for i in range(1, len(x)) if x[i - 1] <= mean < x[i]]
    gaps = [b - a for a, b in zip(edges, edges[1:])]
    usual = sorted(gaps)[len(gaps) // 2]
    return [(a, g) for a, g in zip(edges, gaps) if abs(g - usual) > 1.5]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--binary')
    args = ap.parse_args()
    binary = args.binary or (sorted(glob.glob('derived/install/bin/openmsx')) or [None])[0]
    if not binary:
        sys.exit('openMSX not found; run make install INSTALL_BASE=$PWD/derived/install or pass --binary')
    binary = os.path.abspath(binary)

    work = tempfile.mkdtemp(prefix='record-pause-')
    script = os.path.join(work, 'run.tcl')
    open(script, 'w').write(tcl_script(work))
    cmd = [binary, '-machine', 'C-BIOS_MSX2', '-script', script]
    if not os.environ.get('DISPLAY') and shutil.which('xvfb-run'):
        cmd = ['xvfb-run', '-a'] + cmd
    env = dict(os.environ)
    share = os.path.join(os.path.dirname(os.path.dirname(binary)), 'share')
    if 'OPENMSX_SYSTEM_DATA' not in env and os.path.isdir(os.path.join(share, 'machines')):
        env['OPENMSX_SYSTEM_DATA'] = share
    began = time.time()
    subprocess.run(cmd, env=env, check=True, timeout=120,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wall = time.time() - began

    times = dict(line.split() for line in open(os.path.join(work, 'times.txt')))
    times = {k: float(v) for k, v in times.items()}
    failures = []
    checks = [('paused', 'start', 'stop')] + [(f'piece{k}', f'piece{k}', f'end{k}') for k in range(PIECES)]
    for name, begin, end in checks:
        n, rate = frames(os.path.join(work, f'{name}.wav'))
        want = (times[end] - times[begin]) * rate
        print(f'{name}: {n} samples, emulated {want:.1f} ({n - want:+.1f})')
        if abs(n - want) > 1.5:
            failures.append(name)
    odd = jolts(os.path.join(work, 'paused.wav'))
    print(f'paused: {len(odd)} jolts in the tone {odd[:5]}')
    if odd:
        failures.append('the paused tone')
    print(f'wall-clock {wall:.2f} s')
    if failures:
        sys.exit(f'FAIL: samples lost or added in {", ".join(failures)} ({work})')
    shutil.rmtree(work)
    print('OK')


if __name__ == '__main__':
    main()
