from pipeline.segmenter import Segment, segment, _is_cjk_majority, _paragraphs


class TestCjkDetection:
    def test_english_text(self):
        assert not _is_cjk_majority("Hello world, this is a test.")

    def test_chinese_text(self):
        assert _is_cjk_majority("这是一个测试句子，用来检测中文。")

    def test_mixed_below_threshold(self):
        assert not _is_cjk_majority("Hello world 你好")

    def test_mixed_above_threshold(self):
        assert _is_cjk_majority("你好世界测试 hi")

    def test_empty(self):
        assert not _is_cjk_majority("")

    def test_whitespace_only(self):
        assert not _is_cjk_majority("   \n\n  ")


class TestEnglishSegmenter:
    def test_short_sentences_merge(self):
        body = "Hello. World. This is fine."
        segs = segment("ch01", body)
        assert len(segs) == 1
        assert segs[0].seg_id == "ch01_seg001"

    def test_long_sentence_standalone(self):
        body = "A" * 100 + ". " + "B" * 100 + "."
        segs = segment("ch01", body)
        assert len(segs) == 2

    def test_seg_ids_sequential(self):
        body = ". ".join(["Sentence number " + str(i) + " is here now ok" for i in range(10)]) + "."
        segs = segment("ch01", body)
        for i, seg in enumerate(segs):
            assert seg.seg_id == f"ch01_seg{i + 1:03d}"

    def test_empty_body(self):
        assert segment("ch01", "") == []

    def test_single_sentence(self):
        body = "Just one sentence here that is long enough to stand on its own without merging."
        segs = segment("ch01", body)
        assert len(segs) == 1
        assert segs[0].text == body


class TestParagraphHardSplit:
    def test_no_merge_across_paragraphs(self):
        body = "Short line A.\n\nShort line B."
        segs = segment("ch01", body)
        assert len(segs) == 2
        assert segs[0].text == "Short line A."
        assert segs[1].text == "Short line B."

    def test_chinese_dialogue_paragraphs(self):
        body = '"你好。"她说。\n\n他点了点头。\n\n"再见。"'
        segs = segment("ch01", body)
        assert len(segs) == 3

    def test_merge_within_paragraph_still_works(self):
        body = "A. B. C.\n\nD. E."
        segs = segment("ch01", body)
        assert len(segs) == 2


class TestCjkSegmenter:
    def test_chinese_segments_shorter(self):
        body = "。".join(["这是一个测试句子" for _ in range(20)]) + "。"
        segs = segment("ch01", body)
        for seg in segs:
            assert len(seg.text) <= 110  # CJK_MAX + tolerance

    def test_chinese_splits_on_chinese_punctuation(self):
        s1 = "第一个句子在这里说了很多话" * 2
        s2 = "第二个句子也在这里说了很多话" * 2
        body = f"{s1}。{s2}。"
        segs = segment("ch01", body)
        assert len(segs) >= 2
