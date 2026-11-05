import sys
from types import SimpleNamespace

import numpy as np
import pytest

from langtools_praat.media.portaudio import PortAudioSink
from langtools_praat.media.transport import RenderResult


def test_adapter_callback_timing_and_drain_without_real_device(monkeypatch):
    class Stop(Exception):
        pass

    class Abort(Exception):
        pass

    class Stream:
        def __init__(self, **options):
            self.options = options
            self.time, self.active = 10.0, False

        def start(self):
            self.active = True

        def abort(self):
            self.active = False

        def close(self):
            pass

    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        SimpleNamespace(
            OutputStream=Stream,
            CallbackStop=Stop,
            CallbackAbort=Abort,
        ),
    )
    received = []

    def render(output, dac, status):
        received.append((dac, status))
        output[:] = 0.25
        return RenderResult.DRAIN

    sink = PortAudioSink(48000, 2, render, lambda: None)
    assert sink.stream.options["dtype"] == "float32"
    output = np.zeros((10, 2), dtype=np.float32)
    with pytest.raises(Stop):
        sink.stream.options["callback"](output, 10, SimpleNamespace(outputBufferDacTime=10.1), None)
    assert received == [(10.1, None)]
    assert (output == 0.25).all()
    sink.start()
    assert sink.active()
    assert sink.clock() == 10
    sink.abort()
    sink.close()
