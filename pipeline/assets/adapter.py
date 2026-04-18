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
        style_ref_only: bool = False,
    ) -> None:
        ...

    def generate_baseline(self, out_path: Path) -> None:
        """Phase-A baseline for reference-pipeline backends.

        Default is a no-op so placeholder-style adapters that don't participate
        in the baseline → basic → expressions flow stay simple.
        """
        return

    def drain_matte(self) -> None:
        """Wait for deferred matting to finish (no-op for sync adapters)."""
        return
