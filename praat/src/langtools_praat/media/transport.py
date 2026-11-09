"""Bounded selected playback, independent of Qt and optional sounddevice imports.

The callback consumes prefetched float32 buffers; file I/O/conversion runs on a
producer thread. Position maps the device clock to scheduled output buffer times,
not GUI timer ticks or how far the producer has read ahead.
"""

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from queue import Empty, Full, Queue
from threading import Event, Thread
from typing import Protocol
from uuid import uuid4

import numpy as np

from ..errors import Cancelled, InvalidInput
from ..model.time import SampledAxis


class AudioSource(Protocol):
    id: str
    revision: int
    axis: SampledAxis
    channels: int

    def read_frames(self, first: int, count: int, cancellation: Event | None = None): ...


class RenderResult(Enum):
    CONTINUE = 0
    DRAIN = 1
    ABORT = 2


class OutputSink(Protocol):
    """Factory receives (sample_rate, channels, render, on_finished).

    render(outdata, output_buffer_dac_time, status_message) -> RenderResult.
    clock() and DAC timestamps MUST share one device-time coordinate system.
    """

    def start(self) -> None: ...
    def clock(self) -> float: ...
    def active(self) -> bool: ...
    def abort(self) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class PlaybackPosition:
    session_id: str
    source_id: str
    source_revision: int
    source_time: float
    state: str
    timing_quality: str
    error: str | None = None


@dataclass
class _Session:
    source: AudioSource
    first: int
    last: int
    source_id: str = field(init=False)
    source_revision: int = field(init=False)
    axis: SampledAxis = field(init=False)
    channels: int = field(init=False)
    id: str = field(default_factory=lambda: str(uuid4()))
    queue: Queue = field(default_factory=lambda: Queue(maxsize=8))
    cancel: Event = field(default_factory=Event)
    ready: Event = field(default_factory=Event)
    eof: Event = field(default_factory=Event)
    timeline: deque = field(default_factory=lambda: deque(maxlen=512))
    sink: OutputSink | None = None
    producer: Thread | None = None
    current: object = None
    offset: int = 0
    submitted: int = 0
    state: str = "buffering"
    error: str | None = None
    frozen_frame: float | None = None
    observed_frame: float = 0.0
    terminal: bool = False

    def __post_init__(self):
        self.source_id = self.source.id
        self.source_revision = self.source.revision
        self.axis = self.source.axis
        self.channels = self.source.channels

    def source_changed(self):
        return self.source.id != self.source_id or self.source.revision != self.source_revision


class Transport:
    def __init__(self, sink_factory=None):
        self._factory = sink_factory
        self._session: _Session | None = None

    def play(self, source: AudioSource, start: float, end: float) -> str:
        first, last = source.axis.frame_range(start, end)
        if first == last:
            raise InvalidInput("Select a nonempty audio range to play.")
        self.close()
        session = _Session(source, first, last, submitted=first, observed_frame=float(first))
        self._session = session
        factory = self._factory
        if factory is None:
            from .portaudio import PortAudioSink

            factory = PortAudioSink
        try:
            rate = source.axis.sample_rate
            # Remove reciprocal round-off at the device boundary, not from the
            # source axis. Genuine fractional rates retain their original value.
            nearest = round(rate)
            if nearest > 0 and abs(rate - nearest) <= 1e-6:
                rate = float(nearest)
            session.sink = factory(
                rate,
                source.channels,
                lambda output, dac, status: self._render(session, output, dac, status),
                lambda: self._finished(session),
            )
            session.producer = Thread(target=self._produce, args=(session,), daemon=True)
            session.producer.start()
            session.sink.start()
        except Exception as error:
            session.error = f"Cannot start audio output: {error}"
            session.state = "error"
            session.terminal = True
            self._dispose(session)
            raise InvalidInput(session.error) from error
        return session.id

    @staticmethod
    def _produce(session: _Session):
        try:
            for first in range(session.first, session.last, 4096):
                if session.cancel.is_set():
                    return
                if session.source_changed():
                    raise InvalidInput("Audio source revision changed; restart playback.")
                count = min(4096, session.last - first)
                values = session.source.read_frames(first, count, session.cancel)
                if values.shape != (session.channels, count):
                    raise InvalidInput("Audio source returned an invalid frame buffer.")
                # Frames x channels, conversion/allocation only on the producer.
                block = np.ascontiguousarray(values.T, dtype=np.float32)
                while not session.cancel.is_set():
                    try:
                        session.queue.put(block, timeout=0.05)
                        session.ready.set()
                        break
                    except Full:
                        continue
        except Cancelled:
            pass
        except Exception as error:
            session.error = f"Audio read failed: {error}"
        finally:
            session.eof.set()
            session.ready.set()

    @staticmethod
    def _render(session: _Session, output, dac_time: float, status: str | None):
        output.fill(0)
        if session.cancel.is_set():
            return RenderResult.ABORT
        if status:
            session.error = f"Audio device reported: {status}"
        if session.source_changed():
            session.error = "Audio source revision changed; restart playback."
        if session.error:
            session.state = "error"
            return RenderResult.ABORT
        written = 0
        while written < len(output):
            if session.current is None:
                try:
                    session.current = session.queue.get_nowait()
                    session.offset = 0
                except Empty:
                    if session.eof.is_set() and session.submitted >= session.last:
                        session.state = "draining"
                        return RenderResult.DRAIN
                    session.state = "buffering"
                    return RenderResult.CONTINUE
            count = min(len(output) - written, len(session.current) - session.offset)
            output[written : written + count] = session.current[
                session.offset : session.offset + count
            ]
            session.timeline.append(
                (dac_time + written * session.axis.dx, session.submitted, count)
            )
            written += count
            session.offset += count
            session.submitted += count
            session.state = "playing"
            if session.offset == len(session.current):
                session.current = None
        if session.submitted == session.last:
            session.state = "draining"
            return RenderResult.DRAIN
        return RenderResult.CONTINUE

    @staticmethod
    def _finished(session: _Session):
        # Old callbacks operate only on their captured session, never the new one.
        if not session.terminal:
            if session.error:
                session.state = "error"
            elif not session.cancel.is_set() and session.submitted == session.last:
                session.state = "finished"
                session.frozen_frame = session.last

    @staticmethod
    def _frame_at(session: _Session, now: float) -> float:
        frame = float(session.first)
        for dac, first, count in tuple(session.timeline):
            if now < dac:
                break
            frame = first + max(0.0, min(count, (now - dac) / session.axis.dx))
        return min(session.last, max(session.first, frame))

    def position(self, session_id: str) -> PlaybackPosition:
        session = self._require(session_id)
        if session.terminal:
            frame = session.frozen_frame if session.frozen_frame is not None else session.first
        else:
            try:
                if session.source_changed():
                    session.error = "Audio source revision changed; restart playback."
                frame = self._frame_at(session, session.sink.clock())
                session.observed_frame = frame
                if session.error:
                    session.state = "error"
                if session.state == "finished":
                    frame = session.last
                elif not session.sink.active() and session.state != "error":
                    # Inactivity can precede the finished callback. Re-read the
                    # clock after active(): the first clock sample may predate
                    # completion. Early device loss must still be an error.
                    now = session.sink.clock()
                    if session.state == "finished" or (
                        session.state == "draining"
                        and session.submitted == session.last
                        and session.timeline
                        and now
                        >= session.timeline[-1][0]
                        + session.timeline[-1][2] * session.axis.dx
                        - 1e-9 * session.axis.dx
                    ):
                        session.state = "finished"
                        frame = session.last
                    else:
                        session.error = (
                            "Audio output stopped unexpectedly; check the output device."
                        )
                        session.state = "error"
            except Exception as error:
                frame = session.observed_frame
                session.error = f"Audio device unavailable: {error}"
                session.state = "error"
            if session.state in ("finished", "error"):
                session.frozen_frame = frame
                session.terminal = True
                self._dispose(session)
        # Frame cells, rather than centers, represent the audible sample boundaries.
        time = session.axis.x1 + (frame - 0.5) * session.axis.dx
        return PlaybackPosition(
            session.id,
            session.source_id,
            session.source_revision,
            time,
            session.state,
            "device-clock-estimate",
            session.error,
        )

    def stop(self, session_id: str) -> PlaybackPosition:
        session = self._require(session_id)
        before = self.position(session_id)
        if not session.terminal:
            session.frozen_frame = (before.source_time - session.axis.x1) / session.axis.dx + 0.5
            session.state = "stopped"
            session.terminal = True
            self._dispose(session)
        return self.position(session_id)

    def close(self) -> None:
        if self._session is not None:
            if not self._session.terminal:
                self.stop(self._session.id)
            self._dispose(self._session)

    def _require(self, session_id: str) -> _Session:
        if self._session is None or self._session.id != session_id:
            raise InvalidInput("Playback session is no longer current.")
        return self._session

    @staticmethod
    def _dispose(session: _Session):
        session.cancel.set()
        if session.sink is not None:
            sink, session.sink = session.sink, None
            try:
                sink.abort()
            except Exception as error:
                session.error = session.error or f"Audio cleanup failed: {error}"
            try:
                sink.close()
            except Exception as error:
                session.error = session.error or f"Audio cleanup failed: {error}"
        # No unbounded join on the GUI thread. The daemon stops at the next bounded
        # source read/queue timeout. Each producer owns only its own session state.
