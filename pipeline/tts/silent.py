import io

import numpy as np
import soundfile as sf

from pipeline.tts.adapter import TTSAdapter

SAMPLE_RATE = 24000


def _duration_ms(text: str) -> int:
    return max(1500, len(text) * 55)


def silent_ogg(duration_ms: int) -> bytes:
    n = int(SAMPLE_RATE * duration_ms / 1000)
    samples = np.zeros(n, dtype=np.float32)
    buf = io.BytesIO()
    sf.write(buf, samples, SAMPLE_RATE, format="OGG", subtype="VORBIS")
    return buf.getvalue()


class SilentTTS(TTSAdapter):
    def duration_ms(self, text: str) -> int:
        return _duration_ms(text)

    def synth(self, text: str, voice_id: str = "narrator") -> bytes:
        return silent_ogg(self.duration_ms(text))
