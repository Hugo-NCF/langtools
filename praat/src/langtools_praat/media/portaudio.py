"""Lazy PortAudio adapter; importing core/CLI never imports sounddevice."""

from ..errors import InvalidInput
from .transport import RenderResult


class PortAudioSink:
    def __init__(self, sample_rate, channels, render, finished):
        try:
            import sounddevice as sd
        except (ImportError, OSError) as error:
            raise InvalidInput(
                "Audio output requires sounddevice and PortAudio; install the gui extra."
            ) from error

        def callback(output, frames, timing, status):
            try:
                result = render(output, timing.outputBufferDacTime, str(status) if status else None)
            except Exception:
                output.fill(0)
                raise sd.CallbackAbort from None
            if result is RenderResult.ABORT:
                raise sd.CallbackAbort
            if result is RenderResult.DRAIN:
                raise sd.CallbackStop

        self.stream = sd.OutputStream(
            samplerate=sample_rate,
            channels=channels,
            dtype="float32",
            blocksize=1024,
            callback=callback,
            finished_callback=finished,
            latency="high",
        )

    def start(self):
        self.stream.start()

    def clock(self):
        return self.stream.time

    def active(self):
        return self.stream.active

    def abort(self):
        self.stream.abort()

    def close(self):
        self.stream.close()
