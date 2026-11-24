# akiyan fork additions

This branch is openMSX with a few generic additions and fixes for scripted
and unthrottled runs (automation, fast iteration, capture). Change information,
setup and verification records live in
[akiyan/openMSX-ops](https://github.com/akiyan/openMSX-ops).

## `midi-out-smf`: timestamped MIDI out

A MIDI out pluggable that writes a Standard MIDI File (format 0). Each message
is stamped with the **emulated** time at which its last byte arrived, counted
from the moment the pluggable was plugged in. The host clock is not used, so
the timing survives `set throttle off`.

```tcl
set midi-out-smf-filename /absolute/path/to/out.mid
plug MSX-MIDI-out midi-out-smf
```

- Timing: 25000 ticks per quarter note, tempo 120 bpm, so 1 tick = 20 µs
  (50000 ticks per emulated second).
- Tick 0 is the plug time. A marker meta event `openMSX emutime <seconds>`
  holds that emulated time (as `machine_info time` reports it), to line the
  file up with other output of the same run, such as `record`.
- The file is a complete SMF after every message: the end-of-track event and
  the track length are rewritten each time, so it can be read while the
  emulator runs or after the process was killed.
- SysEx is stored as `F0 <len> <data… F7>`; a SysEx longer than 256 bytes is
  continued with `F7 <len> <data>` packets. Other system common messages are
  stored as `F7` escapes. Realtime messages (F8–FF) are not stored.
- If emulated time goes back (loadstate, reverse) the next delta is 0. Like
  `midi-out-logger`, the plug state is not restored from savestates.

Render the file with any SMF player, for example:

```sh
fluidsynth -ni -F out.wav -r 44100 FluidR3_GM.sf2 out.mid
```

## `record` keeps every sample across pauses

openMSX 22.0 restarted the mixer's sample clock at the current time whenever
the mixer's parameters were set again: at the end of every pause, and at every
start and stop of a recording (`record start`/`record stop` switch the
synchronous mode). The samples since the mixer's last update (up to one
of its buffers) were never generated. A recording over which the emulation
pauses again and again (a script that waits for something at each step) came
out shorter than the emulated time it covers (522 samples, 11.8 ms, a pause on
average in a headless run at 44.1 kHz), and its sound ran ever earlier than
its emulated time. A `record start`/`record stop` pair lost 780 samples of
0.25 s. The sound devices also got new resamplers there, which
start from silence and jolted a tone by up to 12 samples.

- When the mixer's parameters are set again, it first generates the sound up
  to the current time with the old ones, and its clock goes on from there,
  keeping its phase.
- When the sample rate, the speed and the clock's period stay the same (only
  the buffer size changes: a pause's start or end, a recording's start or stop
  at the normal speed), the sound devices keep their resamplers.
- After a savestate is loaded (`loadstate`, `reverse`), the clock still
  restarts at the loaded time, as in 22.0. The first version of this fix
  generated the sound there too, from the new machine's time 0, and crashed
  (akiyan/openMSX#3).

A recording now holds its emulated time to a sample, whatever pauses it spans.
The same change is offered upstream as two commits (sound up to now, keep the
resamplers).

## Tests

```sh
./configure && make -j8
make install INSTALL_BASE=$PWD/derived/install
python3 tests/akiyan/midi_out_smf.py
python3 tests/akiyan/record_pause.py
python3 tests/akiyan/record_loadstate.py
```

`midi_out_smf.py` runs C-BIOS MSX2+ with the `MIDI_Interface_3` extension (the
turbo R GT MIDI ports, no ROM needed), unthrottled, and checks the stored
times, running status, SysEx and a dropped realtime byte.

`record_pause.py` runs C-BIOS MSX2 unthrottled with a PSG tone, records it
over 20 pauses and in 10 pieces, and checks that each WAV holds its emulated
time to a sample and that the tone goes on through the pauses.

`record_loadstate.py` runs C-BIOS MSX2 unthrottled with a PSG tone, records it
across a `loadstate` and a `reverse goback`, and checks that openMSX reaches the
end of the script.
