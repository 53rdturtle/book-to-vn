import pytest
from pipeline.segmenter import Segment

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.__main__ import _expand_says


def _make_segments(n: int, chapter: str = "ch01") -> list[Segment]:
    return [
        Segment(seg_id=f"{chapter}_seg{i + 1:03d}", text=f"Text for segment {i + 1}.")
        for i in range(n)
    ]


def _make_llm_timeline(seg_ids: list[str], chapter: str = "ch01") -> dict:
    commands = [{"type": "bg", "id": "bg_forest_day"}]
    for sid in seg_ids:
        commands.append({"type": "say", "speaker": "narrator", "seg": sid})
    return {"chapter_id": chapter, "title": "Test", "commands": commands}


class TestExpandSays:
    def test_happy_path(self):
        segs = _make_segments(3)
        llm = _make_llm_timeline([s.seg_id for s in segs])
        result = _expand_says(llm, segs)
        says = [c for c in result["commands"] if c["type"] == "say"]
        assert len(says) == 3
        assert says[0]["text"] == "Text for segment 1."
        assert says[0]["voice_clip"] == "ch01_seg001.ogg"
        assert says[2]["voice_clip"] == "ch01_seg003.ogg"

    def test_non_say_commands_preserved(self):
        segs = _make_segments(1)
        llm = _make_llm_timeline([segs[0].seg_id])
        llm["commands"].insert(1, {"type": "bgm_play", "id": "bgm_calm", "fade_ms": 500})
        result = _expand_says(llm, segs)
        assert result["commands"][0] == {"type": "bg", "id": "bg_forest_day"}
        assert result["commands"][1] == {"type": "bgm_play", "id": "bgm_calm", "fade_ms": 500}

    def test_missing_segment_raises(self):
        segs = _make_segments(3)
        llm = _make_llm_timeline(["ch01_seg001", "ch01_seg003"])
        with pytest.raises(SystemExit, match="Segment coverage mismatch"):
            _expand_says(llm, segs)

    def test_extra_segment_raises(self):
        segs = _make_segments(2)
        llm = _make_llm_timeline(["ch01_seg001", "ch01_seg002", "ch01_seg003"])
        with pytest.raises(SystemExit, match="unknown seg_id"):
            _expand_says(llm, segs)

    def test_reordered_segments_raises(self):
        segs = _make_segments(3)
        llm = _make_llm_timeline(["ch01_seg001", "ch01_seg003", "ch01_seg002"])
        with pytest.raises(SystemExit, match="Segment coverage mismatch"):
            _expand_says(llm, segs)
