import numpy as np
import pytest

from langtools_praat.gui.app import MainWindow
from langtools_praat.io.textgrid import write
from langtools_praat.media.fake import FakeSink
from langtools_praat.media.transport import PlaybackPosition, Transport
from langtools_praat.model.textgrid import TextGrid


class Factory:
    def __init__(self):
        self.sinks = []

    def __call__(self, *args):
        sink = FakeSink(*args)
        self.sinks.append(sink)
        return sink


def test_play_stop_cursor_and_no_annotation_mutation(qtbot, wav, tmp_path):
    factory = Factory()
    transport = Transport(factory)
    window = MainWindow(transport)
    qtbot.addWidget(window)
    assert not window.play_action.isEnabled()
    path = tmp_path / "labels.TextGrid"
    write(path, TextGrid.create("labels", 0, 1, (("words", "interval"),)))
    window.open_path(wav)
    window.open_path(path)
    original = window.session.document
    window.region.setRegion((0.25, 0.75))
    window.play_selection()
    assert window.transport._session.ready.wait(2)
    sink = factory.sinks[-1]
    sink.pump(4000)
    sink.advance(0.2)
    window.poll_playback()
    assert window.cursor.value() == pytest.approx(0.35)
    assert window.tier_cursor.value() == pytest.approx(0.35)
    window.stop_playback()
    assert sink.closed
    assert window.cursor.value() == pytest.approx(0.35)
    assert window.session.document is original
    assert not window.session.dirty
    assert window.region.getRegion() == (0.25, 0.75)
    assert not window.stop_action.isEnabled()
    window.close()


def test_source_switch_remove_and_close_stop_output(qtbot, wav, tmp_path):
    import soundfile as sf

    other = tmp_path / "other.wav"
    sf.write(other, np.zeros(16000), 16000, subtype="PCM_16")
    factory = Factory()
    window = MainWindow(Transport(factory))
    qtbot.addWidget(window)
    window.open_path(wav)
    window.play_selection()
    old = factory.sinks[-1]
    window.open_path(other)
    assert old.closed
    window.play_selection()
    sink = factory.sinks[-1]
    window.remove_object()
    assert sink.closed
    assert window.source is None
    assert not window.play_action.isEnabled()
    window.open_path(wav)
    window.play_selection()
    window.close()
    assert factory.sinks[-1].closed


def test_device_error_and_stale_gui_position_do_not_change_cursor(qtbot, wav):
    factory = Factory()
    window = MainWindow(Transport(factory))
    qtbot.addWidget(window)
    window.open_path(wav)
    window.play_selection()
    first_id = window._playback_id
    old_source = window.source
    window.play_selection()
    second_id = window._playback_id
    window.show_playback_position(
        PlaybackPosition(
            first_id,
            old_source.id,
            old_source.revision,
            100,
            "playing",
            "fake",
        )
    )
    assert window.cursor.value() != 100
    assert window._playback_id == second_id
    factory.sinks[-1].running = False
    window.poll_playback()
    assert window._playback_id is None
    assert "unexpectedly" in window.statusBar().currentMessage()
    assert not window.stop_action.isEnabled()
    window.close()


def test_missing_device_and_empty_selection_are_useful_errors(qtbot, wav):
    def missing(*args):
        raise RuntimeError("No output device")

    window = MainWindow(Transport(missing))
    qtbot.addWidget(window)
    window.open_path(wav)
    with pytest.raises(ValueError, match="No output device"):
        window.play_selection()
    assert window._playback_id is None
    window.region.setRegion((0.5, 0.5))
    with pytest.raises(ValueError, match="nonempty"):
        window.play_selection()
    window.close()
