import tempfile
from pathlib import Path

import pytest

from pipeline import cache, cast as cast_mod
from pipeline.assets.adapter import ImageAdapter
from pipeline.assets.placeholders import PlaceholderAdapter


class TestPlaceholderAdapter:
    def test_conforms_to_interface(self):
        assert isinstance(PlaceholderAdapter(), ImageAdapter)

    def test_generate_bg_writes_png(self, tmp_path: Path):
        adapter = PlaceholderAdapter()
        out = tmp_path / "bg_test.png"
        adapter.generate_bg("bg_room_day", "unused description", out)
        assert out.exists()
        assert out.stat().st_size > 0

    def test_generate_char_writes_png(self, tmp_path: Path):
        adapter = PlaceholderAdapter()
        out = tmp_path / "alice" / "smile.png"
        adapter.generate_char("alice", "smile", "unused", out, reference=None)
        assert out.exists()
        assert out.stat().st_size > 0

    def test_generate_char_ignores_reference(self, tmp_path: Path):
        adapter = PlaceholderAdapter()
        out = tmp_path / "bob" / "neutral.png"
        adapter.generate_char("bob", "neutral", "", out, reference=Path("does_not_exist.png"))
        assert out.exists()


class TestCacheBytes:
    def test_round_trip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(cache.config, "CACHE_DIR", tmp_path)
        key = cache.content_key("test", "v1")
        assert cache.get_bytes("img_test", key) is None
        data = b"\x89PNG\r\n\x1a\nfake"
        cache.put_bytes("img_test", key, data)
        assert cache.get_bytes("img_test", key) == data

    def test_different_keys_independent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(cache.config, "CACHE_DIR", tmp_path)
        k1 = cache.content_key("a")
        k2 = cache.content_key("b")
        cache.put_bytes("s", k1, b"one")
        cache.put_bytes("s", k2, b"two")
        assert cache.get_bytes("s", k1) == b"one"
        assert cache.get_bytes("s", k2) == b"two"


class TestCastAppearanceCount:
    def test_bumps_on_char_show_and_say(self):
        cast = {"cast": {}}
        timeline = {
            "commands": [
                {"type": "char_show", "id": "alice", "expr": "neutral", "slot": "left"},
                {"type": "say", "speaker": "alice", "text": "hi", "voice_clip": "x.ogg"},
                {"type": "say", "speaker": "narrator", "text": "...", "voice_clip": "y.ogg"},
                {"type": "say", "speaker": "bob", "text": "yo", "voice_clip": "z.ogg"},
            ]
        }
        updated = cast_mod.update_from_timeline(cast, timeline, "ch01")
        assert updated["cast"]["alice"]["appearance_count"] == 2
        assert updated["cast"]["bob"]["appearance_count"] == 1
        assert "narrator" not in updated["cast"]

    def test_accumulates_across_chapters(self):
        cast = {"cast": {}}
        t1 = {"commands": [{"type": "say", "speaker": "alice", "text": "a", "voice_clip": "x"}]}
        t2 = {"commands": [{"type": "say", "speaker": "alice", "text": "b", "voice_clip": "y"}]}
        c1 = cast_mod.update_from_timeline(cast, t1, "ch01")
        c2 = cast_mod.update_from_timeline(c1, t2, "ch02")
        assert c2["cast"]["alice"]["appearance_count"] == 2
        assert c2["cast"]["alice"]["first_chapter"] == "ch01"

    def test_set_visual_description(self):
        cast = {"cast": {"alice": {"display_name": "alice", "first_chapter": "ch01",
                                    "appearance_count": 5}}}
        updated = cast_mod.set_visual_description(cast, "alice", "tall, red hair")
        assert updated["cast"]["alice"]["visual_description"] == "tall, red hair"
        assert updated["cast"]["alice"]["appearance_count"] == 5
