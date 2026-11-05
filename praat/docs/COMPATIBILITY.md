# Current compatibility and validation limits

This is the initial workflow milestone, not R1 completion. No reference Praat
executable has been pinned or run for this implementation. Tests establish the
listed contracts and self/interchange-format behavior; they do not establish
measured numerical or editor parity with Praat.

## Implemented

- Python 3.12/3.13 package, optional Qt GUI, headless CLI, dependency lock.
- Explicit sampled axes and immutable mono/stereo Sound snapshots.
- File-backed WAV PCM16/24/32 decoding; bounded waveform summaries/cancellation.
- WAV export with explicit nearest-even quantization and saturation at representable
  endpoints, no dither; values outside [-1, 1] are rejected. WAV export cannot retain
  a nonzero time origin. Export supports integer sampling rates only.
- TextGrid full/short/mixed token layouts; UTF-8 and BOM-marked UTF-16 LE/BE;
  LF/CRLF/CR; quotes, multiline text, IPA, scientific notation, raw backslashes,
  precision-safe doubles. Input byte/tier/entity/string limits are configurable.
- Positive-duration/adjacent intervals and ordered unique point times; weaker
  tier/grid domain differences retained with warnings.
- Label edits, collapsed-caret interval split, point insertion, internal boundary
  movement, atomic rejected edits, revision checks, undo/redo and dirty tracking.
- Workspace object rename/copy/remove, atomic no-clobber/explicit replacement saves,
  disk-identity conflict checks when saving back to the originally opened path.
- Desktop open/view/channel selection, zoom/pan, time-region selection, native
  label editor, basic annotation commands and save/unsaved-close handling.
- CLI metadata, new grids, full/short conversion, label editing and annotation CSV.

## Deliberately incomplete

- Acoustic analysis, numerical queries/measurement exports and recording.
- Boundary merge/delete until the D03 discrepancy has a pinned-runtime fixture;
  selected-text/range split, multi-tier drag/snapping, search, add/remove tier UI.
- Independent editor windows, complete menus,
  high-DPI/IME/accessibility parity and original keyboard shortcut parity.
- Complete typed registry/dialog schema, change-set notifications, analysis services.
- Generic object codecs, binary TextGrid, legacy encodings and trigraph rendering.
  Raw trigraph strings survive unchanged; no hidden conversion is performed.
- Zero-duration grid/tier domains are not supported by this model.
- Complete LongSound workflow, codecs beyond the declared WAV subtypes, channels >2.
- Autosave/recovery, bounded undo history, project persistence, native scripting,
  packaging/signing, manual audio-device and full three-platform verification.

Disk conflict detection compares file identity, size and nanosecond modification
time; it is not content hashing or a distributed-filesystem locking protocol. A
concurrent writer can still race the final replacement. Export is atomic on
filesystems supporting same-directory rename/hard links; errors are surfaced.

## Validation evidence

Tests use original synthetic audio and hand-authored fixtures derived from the
public TextGrid format documentation. No original Praat implementation code or
external speech corpus is included. The demo generator creates an original tone;
its IPA labels are text-rendering examples, not phonetic analysis of that tone.

Local checks: 56 pytest behavioral/Qt tests passed, Ruff lint/format passed, and
offscreen desktop render inspected on macOS/arm64 with Python 3.12. Wheel and
source distribution built; a fresh headless wheel install worked without Qt.
[Hosted CI run](https://github.com/mhulden/langtools/actions/runs/41934814868)
passed on Linux, Windows and macOS/Python 3.12 and Linux/Python 3.13. These are
offscreen checks, not manual device or full UI parity verification. Qt tests need
unsandboxed CPU-feature detection on this local host.

Before claiming interoperability, open exported fixture TextGrids in the chosen
Praat release and record the executable hash, settings, expected geometry/text,
and results. Future numerical work must follow the comparison policy in §17 of
the master specification.

## Playback and tier-view increment

[PR #16](https://github.com/mhulden/langtools/pull/16) adds selected playback with
device-clock-derived estimated position, bounded prefetch, explicit errors, and a
linked graphical tier view with click selection and single-mark drag/cancel/undo.
Point moves retain IDs and sorted order; full reference-editor behavior is not
established. See [PLAYBACK_AND_TIERS.md](PLAYBACK_AND_TIERS.md) for supported
operations, test evidence, soft real-time limitations and unmeasured hardware timing.
Microphone input, pinned-reference parity and real-device/loopback validation are
not established by offscreen or fake-sink tests.
