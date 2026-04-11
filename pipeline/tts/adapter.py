from abc import ABC, abstractmethod


class TTSAdapter(ABC):
    @abstractmethod
    def synth(self, text: str, voice_id: str = "narrator") -> bytes:
        ...

    @abstractmethod
    def duration_ms(self, text: str) -> int:
        ...
