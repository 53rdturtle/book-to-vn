import json
import time
from dataclasses import asdict
from pathlib import Path

from pipeline.segmenter import Segment


class BuildLog:
    def __init__(self, out_dir: Path):
        self._dir = out_dir / "logs"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._start = time.time()

    def _write(self, name: str, content: str) -> None:
        (self._dir / name).write_text(content, encoding="utf-8")

    def segments(self, chapter_id: str, segs: list[Segment]) -> None:
        lines = [f"{s.seg_id}  ({len(s.text)} chars)  {s.text}" for s in segs]
        self._write(f"{chapter_id}_segments.txt", "\n".join(lines) + "\n")

    def gemini_prompt(self, chapter_id: str, prompt: str) -> None:
        self._write(f"{chapter_id}_gemini_prompt.txt", prompt)

    def gemini_response(self, chapter_id: str, response: dict, elapsed_s: float) -> None:
        header = f"# Elapsed: {elapsed_s:.2f}s\n\n"
        self._write(
            f"{chapter_id}_gemini_response.json",
            header + json.dumps(response, ensure_ascii=False, indent=2) + "\n",
        )

    def validation_error(self, chapter_id: str, stage: str, message: str) -> None:
        self._write(
            f"{chapter_id}_error_{stage}.txt",
            f"Stage: {stage}\n\n{message}\n",
        )

    def cast_snapshot(self, chapter_id: str, cast: dict) -> None:
        self._write(
            f"{chapter_id}_cast.json",
            json.dumps(cast, ensure_ascii=False, indent=2) + "\n",
        )

    def summary(self, chapters_built: int, total_segments: int) -> None:
        elapsed = time.time() - self._start
        self._write("build_summary.txt", (
            f"Chapters: {chapters_built}\n"
            f"Total segments: {total_segments}\n"
            f"Total time: {elapsed:.2f}s\n"
        ))
