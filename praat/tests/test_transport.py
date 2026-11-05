import threading
from dataclasses import replace
from threading import Event

import numpy as np
import pytest

from langtools_praat.errors import InvalidInput
from langtools_praat.media.fake import FakeSink
from langtools_praat.media.transport import PlaybackPosition, Transport
from langtools_praat.model.time import SampledAxis


class Source:
    def __init__(self, frames=2000, channels=2, origin=3.25):
        self.id, self.revision = "source", 7
        self.axis = SampledAxis.recording(frames, 1000, origin)
        self.channels = channels
        self.values = np.stack([np.arange(frames) / frames] * channels)
        self.requests = []
        self.threads = []

    def read_frames(self, first, count, cancellation=None):
        self.requests.append((first, count))
        self.threads.append(threading.current_thread().name)
        return self.values[:, first : first + count]


class Factory:
    def __init__(self):
        self.sinks = []

    def __call__(self, *args):
        sink = FakeSink(*args)
        self.sinks.append(sink)
        return sink


def ready(transport):
    assert transport._session.ready.wait(2)


def test_selected_samples_channel_layout_and_nonzero_source_clock():
    factory = Factory()
    transport = Transport(factory)
    source = Source()
    session = transport.play(source, 3.75, 4.25)
    ready(transport)
    sink = factory.sinks[-1]
    output = sink.pump(500)
    np.testing.assert_array_equal(output, source.values[:, 500:1000].T.astype(np.float32))
    assert transport.position(session).source_time == 3.75
    sink.advance(0.2)  # 100 ms hardware latency, then 100 ms of actual audio
    assert transport.position(session).source_time == pytest.approx(3.85)
    assert transport.position(session).source_revision == 7
    sink.advance(0.4)
    position = transport.position(session)
    assert position.state == "finished"
    assert position.source_time == 4.25
    assert sink.closed
    assert transport.position(session) == position


def test_stop_freezes_audible_position_not_submitted_or_prefetched_frames():
    factory, source = Factory(), Source()
    transport = Transport(factory)
    session = transport.play(source, 3.25, 5.25)
    ready(transport)
    sink = factory.sinks[-1]
    sink.pump(1000)
    sink.advance(0.35)
    stopped = transport.stop(session)
    assert stopped.state == "stopped"
    assert stopped.source_time == pytest.approx(3.5)
    sink.advance(10)
    assert transport.position(session) == stopped
    assert sink.closed


def test_old_finished_callback_and_session_ids_cannot_change_new_playback():
    factory, source = Factory(), Source()
    transport = Transport(factory)
    first = transport.play(source, 3.25, 4.25)
    ready(transport)
    old_sink = factory.sinks[-1]
    old_sink.pump(1000)
    second = transport.play(source, 4.25, 5.25)
    ready(transport)
    old_sink.finished()
    assert transport.position(second).source_time == 4.25
    with pytest.raises(InvalidInput):
        transport.position(first)
    with pytest.raises(InvalidInput):
        transport.stop(first)
    transport.close()


def test_underrun_silence_does_not_advance_source_clock():
    factory, source = Factory(), Source()
    gate = Event()
    original = source.read_frames

    def waiting_read(*args):
        assert gate.wait(2)
        return original(*args)

    source.read_frames = waiting_read
    transport = Transport(factory)
    session = transport.play(source, 3.25, 4.25)
    sink = factory.sinks[-1]
    assert not sink.pump(100).any()
    sink.advance(1)
    assert transport.position(session).source_time == 3.25
    gate.set()
    ready(transport)
    sink.pump(500)
    sink.advance(0.2)
    assert transport.position(session).source_time == pytest.approx(3.35)
    transport.close()


def test_eof_zero_padding_does_not_add_audio_frames():
    factory, source = Factory(), Source(frames=7, channels=1, origin=0)
    transport = Transport(factory)
    session = transport.play(source, 0, 0.007)
    ready(transport)
    sink = factory.sinks[-1]
    output = sink.pump(16)
    np.testing.assert_array_equal(output[:7, 0], source.values[0].astype(np.float32))
    assert not output[7:].any()
    sink.advance(1)
    assert transport.position(session).source_time == pytest.approx(0.007)
    assert transport.position(session).state == "finished"


def test_bounded_prefetch_and_no_callback_disk_reads():
    factory, source = Factory(), Source(frames=1_000_000)
    transport = Transport(factory)
    session = transport.play(source, 3.25, 1003.25)
    ready(transport)
    sink = factory.sinks[-1]
    sink.pump(1024)
    assert all(count <= 4096 for _, count in source.requests)
    assert all(name != threading.current_thread().name for name in source.threads)
    assert transport._session.queue.maxsize == 8
    transport.stop(session)
    producer = transport._session.producer
    producer.join(timeout=2)
    assert not producer.is_alive()
    assert len(source.requests) <= 11


def test_source_read_error_device_loss_and_start_failure():
    factory, source = Factory(), Source()

    def fail_read(*args):
        raise OSError("source removed")

    source.read_frames = fail_read
    transport = Transport(factory)
    session = transport.play(source, 3.25, 4.25)
    ready(transport)
    factory.sinks[-1].pump()
    position = transport.position(session)
    assert position.state == "error" and "source removed" in position.error
    assert factory.sinks[-1].closed
    source = Source()
    session = transport.play(source, 3.25, 4.25)
    factory.sinks[-1].running = False
    assert transport.position(session).state == "error"

    def unavailable(*args):
        raise RuntimeError("no output device")

    broken = Transport(unavailable)
    with pytest.raises(InvalidInput, match="no output device"):
        broken.play(source, 3.25, 4.25)
    broken.close()


def test_status_error_and_empty_ranges():
    factory, source = Factory(), Source()
    transport = Transport(factory)
    with pytest.raises(InvalidInput):
        transport.play(source, 3.25, 3.25)
    session = transport.play(source, 3.25, 4.25)
    ready(transport)
    factory.sinks[-1].pump(status="output underflow")
    assert transport.position(session).state == "error"
    transport.close()


def test_playback_position_is_immutable():
    position = PlaybackPosition("session", "source", 0, 1.5, "playing", "fake")
    assert replace(position, state="stopped").source_time == 1.5


def test_lost_clock_preserves_last_observed_position_and_cleanup_errors_do_not_crash():
    factory, source = Factory(), Source()
    transport = Transport(factory)
    session = transport.play(source, 3.25, 5.25)
    ready(transport)
    sink = factory.sinks[-1]
    sink.pump(1000)
    sink.advance(0.4)
    observed = transport.position(session)

    def missing_clock():
        raise RuntimeError("device disconnected")

    def missing_close():
        raise RuntimeError("already gone")

    sink.clock = missing_clock
    sink.close = missing_close
    position = transport.position(session)
    assert position.source_time == observed.source_time
    assert position.state == "error"
    assert "disconnected" in position.error
    transport.close()


def test_changed_source_revision_aborts_and_preserves_captured_identity():
    factory, source = Factory(), Source()
    transport = Transport(factory)
    session = transport.play(source, 3.25, 4.25)
    ready(transport)
    source.revision = 8
    position = transport.position(session)
    assert position.source_revision == 7
    assert position.state == "error"
    assert factory.sinks[-1].closed
