import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.__main__ import _validate_asset_ids, AssetIdError


def _timeline_with(commands: list[dict]) -> dict:
    return {"chapter_id": "ch01", "title": "Test", "commands": commands}


class TestAssetIdValidation:
    def test_valid_ids_pass(self):
        tl = _timeline_with([
            {"type": "bg", "id": "bg_forest_day"},
            {"type": "bgm_play", "id": "bgm_calm"},
            {"type": "se", "id": "se_wind"},
        ])
        _validate_asset_ids(tl)

    def test_bg_ids_are_free_form(self):
        # Pass B mints bg_ids (e.g. bg_huaguo_waterfall); they're not catalog-gated.
        tl = _timeline_with([{"type": "bg", "id": "bg_invented_place"}])
        _validate_asset_ids(tl)

    def test_unknown_bgm_raises(self):
        tl = _timeline_with([{"type": "bgm_play", "id": "bgm_unknown"}])
        with pytest.raises(AssetIdError, match="bgm 'bgm_unknown'"):
            _validate_asset_ids(tl)

    def test_unknown_se_raises(self):
        tl = _timeline_with([{"type": "se", "id": "se_invented"}])
        with pytest.raises(AssetIdError, match="se 'se_invented'"):
            _validate_asset_ids(tl)

    def test_character_ids_not_checked(self):
        tl = _timeline_with([
            {"type": "char_show", "id": "any_character", "expr": "neutral", "slot": "left"},
            {"type": "char_hide", "id": "any_character"},
        ])
        _validate_asset_ids(tl)

    def test_multiple_unknown_collected(self):
        tl = _timeline_with([
            {"type": "bgm_play", "id": "bgm_bad"},
            {"type": "se", "id": "se_bad"},
        ])
        with pytest.raises(AssetIdError, match="bgm 'bgm_bad'.*se 'se_bad'"):
            _validate_asset_ids(tl)

    def test_empty_timeline_passes(self):
        _validate_asset_ids(_timeline_with([]))
