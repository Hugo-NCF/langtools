# Implementation status and handoff

Owner of this initial implementation: @jaas29. Tracking: [issue #9](https://github.com/mhulden/langtools/issues/9). Branch: `praat/initial-workflow`.

Implementation: [PR #10](https://github.com/mhulden/langtools/pull/10), ready for review. Check it before starting overlapping work; the files below are implemented on that branch, not yet merged into upstream main.

| Area | Implemented | Main paths |
|---|---|---|
| Setup | Package, locked dependencies, optional GUI, cross-platform test workflow | `pyproject.toml`, `uv.lock`, `.github/workflows/praat.yml` |
| Audio/time | Immutable Sound and explicit axes; bounded WAV reads and waveform summaries; explicit PCM export | `model/time.py`, `model/sound.py`, `io/audio.py`, `media/waveform.py` |
| Annotation | Frozen models; full/short text codec; label/split/point/move commands; undo/redo | `model/textgrid.py`, `io/textgrid.py`, `annotation/` |
| Shared workflow | Object lifecycle, save/conflict checks, registered edits, headless commands | `application/workspace.py`, `cli.py` |
| Desktop | Open WAV/TextGrid, waveform/channel/range controls, native label table, basic edits, save and dirty-close checks | `gui/` |
| Evidence | Original synthetic fixture generator, behavioral/GUI tests, rendered desktop inspection | `tests/`, `examples/make_demo.py` |

Read [INTERFACES.md](INTERFACES.md) before building against these modules and [COMPATIBILITY.md](COMPATIBILITY.md) before describing feature support. Run instructions are in [README.md](../README.md).

The canonical requirements remain in `../REIMPLEMENTATION.md`. This file tracks implementation, not a replacement specification. R1 is not complete; no oracle parity is claimed.

Validation on 2026-11-04: 56 local tests passed, lint/format passed, and a built wheel ran headlessly in a fresh environment. [Hosted CI passed](https://github.com/mhulden/langtools/actions/runs/41934814868) on macOS/Windows/Linux with Python 3.12 and Linux with Python 3.13.

## Follow-up work available

- Pin and capture the reference executable; resolve D03 and other runtime discrepancies.
- Acoustic analysis: Gaussian spectrogram, filtered-AC pitch, Burg formants and intensity. Agree result/settings types before implementation.
- Playback and graphical single-mark tier editing: @jaas29, [PR #16](https://github.com/mhulden/langtools/pull/16), [issue #15](https://github.com/mhulden/langtools/issues/15). See [contracts and validation](PLAYBACK_AND_TIERS.md); check the PR before overlapping `media/` or `gui/` work. Advanced gestures/search and measured device timing remain follow-ups.
- Project recovery, packaged distributions and accessibility/manual platform checks.

Claim a small issue with acceptance criteria, name the modules/interfaces affected, and open a linked draft PR early. Other students should branch from upstream main after this foundation merges, or explicitly coordinate a dependency on this branch. Do not copy modules between forks or create a second TextGrid serializer.
