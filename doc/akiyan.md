# akiyan fork additions

This branch is openMSX with a few generic additions for scripted and
unthrottled runs (automation, fast iteration, capture). Change information,
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

## Tests

```sh
./configure && make -j8
make install INSTALL_BASE=$PWD/derived/install
python3 tests/akiyan/midi_out_smf.py
```

The test runs C-BIOS MSX2+ with the `MIDI_Interface_3` extension (the turbo R
GT MIDI ports, no ROM needed), unthrottled, and checks the stored times,
running status, SysEx and a dropped realtime byte.
