#!/usr/bin/env python3
"""Check the midi-out-smf pluggable against a ROM-free machine.

Runs openMSX unthrottled on C-BIOS MSX2+ with the "MIDI Interface 3"
extension (the turbo R GT MIDI ports at 0xE8-0xEF, no BIOS ROM needed),
sends MIDI bytes through the 8251 at fixed emulated times, and checks that
the Standard MIDI File keeps those emulated times although the run takes
only a fraction of them in wall-clock time.

    make install INSTALL_BASE=$PWD/derived/install
    python3 tests/akiyan/midi_out_smf.py [--binary derived/install/bin/openmsx]
"""

import argparse
import glob
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time

# (emulated seconds after the start of sending, bytes written to the 8251)
SCHEDULE = [
    (0.000, [0xF0, 0x41, 0x10, 0x42, 0x12, 0x40, 0x00, 0x7F, 0x00, 0x41, 0xF7]),
    (0.500, [0x90, 60, 100]),
    (1.000, [0x80, 60, 0]),
    (1.500, [0x90, 64, 100, 67, 100]),   # running status: two note-ons
    (2.000, [0xB0, 7, 90]),
    (2.000 + 0.004, [0xFE]),             # active sensing: not stored
    (3.250, [0x80, 64, 0, 0x80, 67, 0]),
]
START = 1.0          # emulated seconds after boot when sending starts
BYTE_TIME = 10 / 31250  # one MIDI byte on the wire: 320 us


def tcl_script(smf_path, done_path):
    lines = [
        'set renderer none',
        'set throttle off',
        f'set midi-out-smf-filename {{{smf_path}}}',
        'plug MSX-MIDI-out midi-out-smf',
        'proc io {p v} { debug write ioports $p $v }',
        # 8253 counter 0: mode 3, divisor 8 (4 MHz -> 500 kHz = 31250 x 16)
        # 8251: reset, mode x16 8N1, command TxEN|DTR|RxEN|RTS
        'after time 0.5 { io 0xEF 0x36; io 0xEC 8; io 0xEC 0; '
        'io 0xE9 0; io 0xE9 0; io 0xE9 0; io 0xE9 0x40; io 0xE9 0x4E; io 0xE9 0x37 }',
    ]
    for at, data in SCHEDULE:
        for i, b in enumerate(data):
            # leave room for each byte to leave the 8251 before the next one
            t = START + at + i * 2 * BYTE_TIME
            lines.append(f'after time {t:.6f} {{ io 0xE8 {b} }}')
    end = START + SCHEDULE[-1][0] + 1.0
    lines.append(f'after time {end:.6f} {{ unplug MSX-MIDI-out; '
                 f'set f [open {{{done_path}}} w]; puts $f [machine_info time]; close $f; exit }}')
    return '\n'.join(lines) + '\n'


def read_varlen(data, pos):
    value = 0
    while True:
        b = data[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if not b & 0x80:
            return value, pos


def parse_smf(path):
    data = open(path, 'rb').read()
    assert data[:4] == b'MThd', 'no MThd'
    length, fmt, ntrk, division = struct.unpack('>IHHH', data[4:14])
    assert (length, fmt, ntrk, division) == (6, 0, 1, 25000), (length, fmt, ntrk, division)
    assert data[14:18] == b'MTrk', 'no MTrk'
    (tlen,) = struct.unpack('>I', data[18:22])
    assert 22 + tlen == len(data), f'track length {tlen} vs file {len(data)}'
    pos, tick, events = 22, 0, []
    while pos < len(data):
        delta, pos = read_varlen(data, pos)
        tick += delta
        status = data[pos]
        if status == 0xFF:
            kind = data[pos + 1]
            n, p = read_varlen(data, pos + 2)
            events.append((tick, 'meta', kind, data[p:p + n]))
            pos = p + n
        elif status in (0xF0, 0xF7):
            n, p = read_varlen(data, pos + 1)
            events.append((tick, 'sysex', status, data[p:p + n]))
            pos = p + n
        else:
            size = 2 if 0xC0 <= status < 0xE0 else 3
            events.append((tick, 'midi', status, data[pos:pos + size]))
            pos += size
    return events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--binary')
    args = ap.parse_args()
    binary = args.binary or (sorted(glob.glob('derived/install/bin/openmsx')) or [None])[0]
    if not binary:
        sys.exit('openMSX not found; run make install INSTALL_BASE=$PWD/derived/install or pass --binary')
    binary = os.path.abspath(binary)

    work = tempfile.mkdtemp(prefix='midi-out-smf-')
    smf, done = os.path.join(work, 'out.mid'), os.path.join(work, 'done.txt')
    script = os.path.join(work, 'run.tcl')
    open(script, 'w').write(tcl_script(smf, done))
    cmd = [binary, '-machine', 'C-BIOS_MSX2+', '-ext', 'MIDI_Interface_3', '-script', script]
    if not os.environ.get('DISPLAY') and shutil.which('xvfb-run'):
        cmd = ['xvfb-run', '-a'] + cmd
    env = dict(os.environ)
    # an installed tree keeps its data next to bin/ (the built-in path is
    # the configured INSTALL_BASE, /opt/openMSX by default)
    share = os.path.join(os.path.dirname(os.path.dirname(binary)), 'share')
    if 'OPENMSX_SYSTEM_DATA' not in env and os.path.isdir(os.path.join(share, 'machines')):
        env['OPENMSX_SYSTEM_DATA'] = share
    started = time.monotonic()
    subprocess.run(cmd, cwd=work, env=env, timeout=120, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, check=False)
    wall = time.monotonic() - started
    assert os.path.exists(done), 'script did not finish'
    emulated = float(open(done).read())

    events = parse_smf(smf)
    metas = [e for e in events if e[1] == 'meta']
    assert metas[0][2] == 0x51 and metas[0][3] == bytes([0x07, 0xA1, 0x20]), metas[0]
    assert metas[1][2] == 0x06 and metas[1][3].startswith(b'openMSX emutime '), metas[1]
    plug_time = float(metas[1][3].split()[-1])
    assert events[-1][1:3] == ('meta', 0x2F), 'missing end of track'

    msgs = [e for e in events if e[1] in ('midi', 'sysex')]
    # (schedule entry, index of the message's last byte in that entry, kind, data)
    expected = [
        (0, 10, 'sysex', 0xF0, bytes(SCHEDULE[0][1][1:])),
        (1, 2, 'midi', 0x90, bytes([0x90, 60, 100])),
        (2, 2, 'midi', 0x80, bytes([0x80, 60, 0])),
        (3, 2, 'midi', 0x90, bytes([0x90, 64, 100])),
        (3, 4, 'midi', 0x90, bytes([0x90, 67, 100])),
        (4, 2, 'midi', 0xB0, bytes([0xB0, 7, 90])),
        (6, 2, 'midi', 0x80, bytes([0x80, 64, 0])),
        (6, 5, 'midi', 0x80, bytes([0x80, 67, 0])),
    ]
    assert [m[1:] for m in msgs] == [e[2:] for e in expected], msgs
    worst = 0.0
    for (tick, *_), (entry, last, *_) in zip(msgs, expected):
        # a message is stamped when its last byte has left the 8251, so it
        # trails the write of that byte by about one byte time on the wire
        written = START + SCHEDULE[entry][0] + last * 2 * BYTE_TIME
        diff = plug_time + tick / 50000 - written
        worst = max(worst, abs(diff))
        assert 0 <= diff <= 2 * BYTE_TIME + 0.00002, (tick, entry, last, diff)
    assert wall < emulated, f'not unthrottled: wall {wall:.2f}s, emulated {emulated:.2f}s'
    print(f'ok: {len(msgs)} messages, latest {worst * 1000:.3f} ms after the write, '
          f'emulated {emulated:.2f} s in {wall:.2f} s wall, plugged at {plug_time:.6f}')
    shutil.rmtree(work)


if __name__ == '__main__':
    main()
