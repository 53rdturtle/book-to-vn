from abc import ABC, abstractmethod
from pathlib import Path


class ImageAdapter(ABC):
    @abstractmethod
    def generate_bg(self, bg_id: str, description: str, out_path: Path) -> None:
        ...

    @abstractmethod
    def generate_char(
        self,
        char_id: str,
        expr: str,
        description: str,
        out_path: Path,
        reference: Path | None = None,
    ) -> None:
        ...
