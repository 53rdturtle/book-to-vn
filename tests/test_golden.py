import json
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.__main__ import _expand_says
from pipeline.assets.placeholders import scan
from pipeline.segmenter import Segment

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


SEGMENTS = [
    Segment("ch01_seg001", "The path wound through the ancient wood, dappled light filtering through leaves that whispered secrets to each other."),
    Segment("ch01_seg002", "Mara paused at the fork, her lantern casting long shadows across the mossy stones beneath her feet."),
    Segment("ch01_seg003", "An old man sat on a stump, whittling a piece of wood. He looked up as she approached and smiled warmly."),
    Segment("ch01_seg004", "\"I'm looking for the village beyond the ridge,\" Mara said, holding her lantern higher to see his face."),
    Segment("ch01_seg005", "A gust of wind swept through the clearing, and for a moment the lantern flickered as if it might go out entirely."),
]


class TestGoldenExpansion:
    def test_expand_matches_expected(self):
        llm = _load("ch01_llm.json")
        expected = _load("ch01_expected.json")
        result = _expand_says(llm, SEGMENTS)
        assert result == expected

    def test_asset_scan(self):
        expanded = _load("ch01_expected.json")
        manifest = scan(expanded)
        assert "bg_forest_day" in manifest.bgs
        assert ("mara", "neutral") in manifest.chars
        assert ("mara", "surprised") in manifest.chars
        assert ("old_man", "smile") in manifest.chars
        assert "bgm_calm" in manifest.bgms
        assert "se_wind" in manifest.ses
