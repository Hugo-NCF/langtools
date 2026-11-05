# Playback and graphical tiers

Owner: @jaas29. [Issue #15](https://github.com/mhulden/langtools/issues/15), [PR #16](https://github.com/mhulden/langtools/pull/16), coordinated in [discussion #11](https://github.com/mhulden/langtools/discussions/11).

This branch implements the injectable transport, bounded PortAudio playback, GUI controls and graphical interval/point tiers. Acoustic analysis and shared analysis settings/results remain @Comet-Chaser-99's lane. It reuses the existing TextGrid codec and leaves D03 merging unresolved.

## Implemented workflow

- Select a nonempty range on the waveform or click an interval on a tier. Play/Stop
  uses that range; Tab toggles only when a plot has focus. The native annotation
  table retains its text editing/navigation behavior.
- An orange cursor tracks output position in both plots. Stop freezes that cursor
  without destroying the selected range or changing annotation content.
- A single batched tier item draws visible intervals/points with clipped labels.
  Tier names live on the left axis, and both plots have matching axis widths and
  linked time coordinates. Native table selection and plot selection synchronize.
- Internal boundaries and points have single-mark drag previews. Release validates
  and commits through the workspace registry using the captured revision. Escape,
  crossing boundaries, duplicate point times or stale revisions produce no partial
  edits. A successful drag is one undo step. Point crossing reorders by time while
  retaining point IDs; its exact reference-editor selection behavior is still D06.
- Source switch/removal and accepted window close stop output. Device/read errors
  are reported without losing edits; old session callbacks cannot update new ones.

## Headless transport contract

`media.transport.Transport(sink_factory=None)` accepts an optional factory; default
output lazily imports `media.portaudio.PortAudioSink`. No Qt/device library is
initialized by importing the core, CLI or transport module.

```python
from pathlib import Path
from langtools_praat.io.audio import AudioFile
from langtools_praat.media.transport import Transport

source = AudioFile.open(Path("recording.wav"))
transport = Transport()  # no device opened until play()
session_id = transport.play(source, 0.25, 0.75)
position = transport.position(session_id)
# ... caller/event loop polls and eventually stops or observes finished/error ...
stopped = transport.stop(session_id)
transport.close()
```

- AudioSource: stable `id`, `revision`, `axis`, `channels` and
  `read_frames(first, count, cancellation)` returning channel-first data.
- A session captures ID/revision/axis/channel metadata. Revision changes abort;
  stale session IDs are rejected. The existing immutable AudioFile implements
  this interface without modification.
- `play` maps the requested range through `SampledAxis.frame_range` (centers in
  `[start,end)`) and rejects ranges with no samples. Playback position maps frame
  *cell boundaries* through `x1 + (frame - 0.5)*dx`. Domain origins are preserved;
  source/file-rate metadata does not get replaced with elapsed wall time.
- Position is a frozen `PlaybackPosition(session_id, source_id, source_revision,
  source_time, state, timing_quality, error)`. States are buffering, playing,
  draining, finished, stopped, error. Repeated terminal reads are stable.
- Factory: `(sample_rate, channels, render, on_finished) -> OutputSink`.
  Sink methods: `start`, `clock`, `active`, `abort`, `close`. The render callback
  receives a writable frame-major float32 buffer, its first frame's DAC timestamp,
  and optional device status; it returns CONTINUE, DRAIN or ABORT.
- Sink clock and DAC timestamps must use the same device-time coordinate system.
  `media.fake.FakeSink` exposes deterministic buffer scheduling/clock advancement
  and is used in tests; its captured buffer history is a test aid, not a streaming
  production cache.

## Timing and bounded buffering

A producer thread reads at most 4,096 source frames per operation, converts to
float32 and prefetches at most eight queued blocks. The callback consumes those
blocks with nonblocking queue reads; it does not read files, decode audio, call Qt,
or change annotations. Source data remains untouched. Playback uses the source
sample rate and preserves all channels; unsupported device formats/rates surface
as output errors instead of implicit resampling/downmixing.

The transport keeps a bounded mapping from output DAC timestamps to source-frame
segments. `position()` evaluates the output stream clock against that map. Silence
inserted while waiting for prefetch does not advance source time; EOF zero-padding
does not extend the selected source range. A device-clock failure retains the last
observed cursor position. Underflow/device status errors abort rather than claiming
uninterrupted timing.

The GUI's 40 ms timer only samples this position; it never computes audio progress
from timer ticks. Timing is labeled **device-clock-estimate**: host-reported DAC
times are not an acoustic loopback measurement. Late stop/polling, driver buffering,
hardware latency and platform differences still need measurement. There is no
published millisecond-accuracy claim. Python queues/allocations/GIL make this a
soft real-time prototype, not a hard real-time or lock-free engine. Stream opening
can briefly block on driver initialization. Cleanup cancels producer work without
an unbounded GUI-thread join; a producer exits after its current bounded read or
queue timeout. OS/filesystem stalls cannot be guaranteed to complete immediately.

## Tier view and integration boundaries

`gui.tiers.TierCanvas(workspace)` takes `set_session(session)`. It emits
`selected_entity(tier_id, entity_id)`, `edited()` and `failed(message)`. Its cached
sorted time index supports bisect-based hit tests. Rendering slices only visible
entities and reduces detail when density exceeds roughly two entities per pixel;
zooming in restores exact boundaries/labels. One graphics item represents all tiers.
There is no QGraphicsItem per annotation. Building/replacing indices and the native
table still costs O(number of annotations); full 100,000-entity editing performance
has not been benchmarked. Display clipping never truncates stored labels.

New registry command: `annotation.move_point(tier_id, point_id, time)`. It retains
stable IDs, sorts by time, rejects duplicate times and enforces the tier domain.
Existing `annotation.move_boundary` handles interval drags unchanged. No automatic
snapping, grouped multi-tier drag, merge/delete, tier management/search or complete
original keyboard/focus parity is claimed.

For the analysis contributor: `gui/app.py` now contains `self.tiers`, the linked
waveform `self.plot`, `self.cursor`/`self.tier_cursor`, and `self.transport`.
Selection remains `self.region`; playback cursor updates do not mutate it.
Coordinate any further shared GUI changes through the discussion/PRs before
integration. No analysis types/settings have been added here.

## Validation

79 local tests pass on macOS/arm64 with Python 3.12, including deterministic clock
and latency behavior, selected stereo samples, bounded reads, errors, old callbacks,
source revisions, device cleanup, GUI lifecycle, actual mouse drag, Unicode/table
synchronization, dense-tier rendering, revision rejection, Escape and undo. Ruff
lint/format pass; offscreen editor screenshot is in `screenshots/playback-and-tiers.png`.

The sounddevice wrapper is exercised using a simulated OutputStream. Real speakers,
device loss, acoustic loopback latency and pinned Praat playback/editor comparisons
remain manual validation tasks. CI runs offscreen/fake-sink tests on all three OSes;
hosted results are reported on the PR, not inferred from this configured coverage.

Primary API references:

- [sounddevice streams, DAC timestamps, callback constraints and drain behavior](https://python-sounddevice.readthedocs.io/en/0.5.3/api/streams.html)
- [Qt timers](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QTimer.html)
- [Praat TextGridEditor interaction manual](https://praat.org/manual/TextGridEditor.html)
