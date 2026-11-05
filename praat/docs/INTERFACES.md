# Shared interfaces for the next contributors

The implemented subset follows master specification §3, §4, §11 and §16. Import
these modules directly; do not introduce parallel Sound or TextGrid definitions.

## Audio and time

- `model.time.SampledAxis(xmin, xmax, nx, dx, x1)` preserves domain and sample
  centers separately. Public sample numbers are one-based/fractional; array
  indices are zero-based. `recording(frames, rate, xmin=0)` sets the half-sample origin.
- `axis.time(number)` and `axis.sample_number(time)` do not round or clip queries.
  `axis.frame_range(start, end)` returns a clipped zero-based half-open slice for
  centers in `[start, end)`. Positions within 1e-9 samples of an integer snap to
  that integer. This is a declared project convention, not global Praat parity.
- `model.sound.Sound(name, axis, samples)` owns immutable float64 data shaped
  `(channels, frames)`. Inputs are copied into bytes-backed read-only storage.
- `io.audio.AudioFile.open(path, xmin=0)` is a file-backed source with stable `id`,
  `revision=0`, `axis`, `channels`, `subtype`, and `describe()` metadata.
- `source.read_frames(first, count, cancellation=None)` returns immutable
  `(channels, count)` floats. No resampling, normalization or downmix. Each read
  checks the source's disk identity. Cancellation is a `threading.Event`.
- `source.load(max_bytes=256*1024*1024)` explicitly requests a complete Sound.
  `media.waveform.summarize` instead reads at most 65,536 frames per block and
  returns peak-preserving min/max buckets with source-time coordinates.
- Channels are zero-based in Python. GUI labels and CLI tier/item positions are
  one-based. Do not guess array orientation from libsndfile's frame-major layout.

## Annotations and edits

- `model.textgrid` defines frozen `TextGrid`, `IntervalTier`, `PointTier`,
  `Interval`, `Point`. Containers become tuples; IDs are unique within a document.
  Duplicate names are valid. Labels retain their literal decoded content.
- Strong geometry invariants are validated at construction. Weak domain mismatches
  remain representable and appear in `document.domain_warnings()`.
- `io.textgrid.loads/dumps/read/write` are the only TextGrid codec. `write` defaults
  to no-clobber; replacing an existing file requires `overwrite=True`.
- `annotation.session.AnnotationSession` owns a document snapshot, monotonic revision,
  undo/redo stacks, and saved/dirty state. Failed edits publish no partial result.
- `application.workspace.Workspace` owns file-backed audio sources and annotation
  sessions by ID. `open`, `get`, `add_annotation`, `rename`, `copy`, `remove`, and
  `save` are shared with the desktop. New annotations/copies are unsaved.
- `Workspace.execute(id, command_id, expected_revision=..., **parameters)` dispatches
  the registered annotation commands and returns the updated `TextGrid` snapshot.

| Command ID | Parameters |
|---|---|
| `annotation.set_label` | `tier_id`, `entity_id`, `label` |
| `annotation.split_interval` | `tier_id`, `time`, `text_offset=0` |
| `annotation.insert_point` | `tier_id`, `time`, `label=""` |
| `annotation.move_boundary` | `tier_id`, `right_interval_id`, `time` |
| `annotation.move_point` | `tier_id`, `point_id`, `time` |

`text_offset` counts Python Unicode code points, not Qt UTF-16 offsets or grapheme
clusters. Only collapsed-caret splitting is implemented. Selected-text/range
splitting, boundary removal (D03), multi-tier drag and complete registry parameter
schemas need their own issues and reference fixtures.

Undo/redo restores immutable content snapshots with a new revision; a revision
never goes backward. Undoing back to saved content clears dirty state. Snapshot
history is deliberately simple and unbounded in this prototype; profile and
bound it before claiming large-corpus editing support.

## Analysis contracts

`analysis.contracts` defines the shared settings, request and result types for
R1 analyses (issue #12). It contains no algorithms. Each analysis is a pure
function from a Sound/frames and an `AnalysisRequest` to one result type.

- **Request:** `AnalysisRequest(source_id, revision, settings, time_range,
  channels, token)`. `TimeRange` is half-open `[start, end)` in source seconds,
  stored as canonical floats. Every result's frame centres must lie in that range
  (within 1e-9 frame steps, matching `frame_range`), so a result in local or wrong
  coordinates is rejected at construction. Its declared domain may be wider.
  `ChannelPolicy.single(i)` is zero-based. `ChannelPolicy.combined()` means the
  method's documented multichannel rule; a method without one rejects it. There is
  no implicit downmix.
- **Settings:** `SpectrogramSettings`, `PitchSettings` (filtered AC),
  `FormantSettings` (Burg) and `IntensitySettings` are frozen and validated.
  `time_step=None` means automatic; the result's axis records the resolved
  step. Defaults are reference *candidates* from the manual and source until the
  pinned executable is captured.
- **Provenance:** `Provenance(request, Algorithm(id, version))`. `record()` is
  JSON for export. `cache_key` hashes source identity, revision, channel policy,
  range, every setting and the algorithm version, and excludes the request token.
  Bump `Algorithm.version` whenever numerical output can change.
- **Stale results:** every result carries its provenance. `accept(result,
  source_id, revision, token)` is the single check a GUI uses to drop stale or
  out-of-order results.

| Result | Axes | Arrays (axis 0 = frame) | Units | Undefined |
|---|---|---|---|---|
| `Spectrogram` | `time`, `frequency` (both `SampledAxis`) | `power (frames, bins)`, `valid` | Pa²/Hz, linear | NaN + `valid=False` |
| `Pitch` | `time`; `ceiling` Hz | `candidate_frequency/strength (frames, slots)`, `candidate_count`, `chosen`, `intensity` | Hz, ratio | unvoiced candidate = NaN Hz; `voiced` is `0 < f < ceiling` |
| `Formant` | `time` | `frequency/bandwidth (frames, slots)` sorted, `count`, `intensity` | Hz, Pa² | slots ≥ `count` are NaN |
| `Intensity` | `time` | `values (frames,)` | dB re 2e-5 Pa | NaN, `valid` derived |

Stored values are always physical. For the spectrogram, dB conversion, display
pre-emphasis, autoscaling, dynamic range and grey mapping are separate pure
display/query functions, so GUI and CLI read identical numbers. This does not apply
to analysis preprocessing: Burg formants must still pre-emphasise the signal
numerically from `FormantSettings.preemphasis_from` before LPC. Arrays are read-only, like `Sound.samples`.

## UI and future analysis

- The optional `gui` package alone imports Qt/PyQtGraph. Headless imports work
  without the GUI extra or a display/device.
- The editor combines a waveform, graphical single-mark tier editing and a native
  editable annotation table. Full tier gestures and independent-editor lifecycle
  remain follow-ups.
- Worker waveform results carry a generation token; viewport/source changes
  cancel and invalidate previous requests immediately.
- Add analysis as independent pure functions behind a service that accepts an
  immutable source revision and explicit settings, using the analysis contracts above.
- `media.transport.Transport` provides selected playback via an injectable sink,
  a deterministic fake and lazy sounddevice output. `gui.tiers.TierCanvas` renders
  and edits annotations through the same registry. See
  [PLAYBACK_AND_TIERS.md](PLAYBACK_AND_TIERS.md) for integration contracts and limits;
  the cursor is device-clock-derived, not advanced by GUI timer ticks.

These Python spellings implement a subset of §16's language-neutral examples;
they are not APIs of original Praat. Preserve compatibility with these consumers
or coordinate an interface-changing PR explicitly.
