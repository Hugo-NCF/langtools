"""Deterministic audio sink for tests and integration without an output device."""

import numpy as np

from .transport import RenderResult


class FakeSink:
    def __init__(self, sample_rate, channels, render, finished, *, latency=0.1):
        self.rate, self.channels = sample_rate, channels
        self.render, self.finished = render, finished
        self.latency = latency
        self.now = 0.0
        self.running = False
        self.closed = False
        self.drain_at = None
        self.buffers = []

    def start(self):
        self.running = True

    def clock(self):
        return self.now

    def active(self):
        return self.running

    def pump(self, frames=1024, status=None):
        """Schedule one buffer at the fake clock + declared device latency."""
        output = np.zeros((frames, self.channels), dtype=np.float32)
        result = self.render(output, self.now + self.latency, status)
        self.buffers.append(output.copy())
        if result is RenderResult.DRAIN:
            self.drain_at = self.now + self.latency + frames / self.rate
        elif result is RenderResult.ABORT:
            self.running = False
            self.finished()
        return output

    def advance(self, seconds):
        self.now += seconds
        if self.drain_at is not None and self.now >= self.drain_at:
            self.running = False
            self.finished()
            self.drain_at = None

    def abort(self):
        self.running = False

    def close(self):
        self.closed = True
        self.running = False
