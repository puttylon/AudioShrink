#!/usr/bin/env python3
"""Tests for AudioShrink (standard library, no pytest required).

Run:  python3 -m unittest test_audioshrink -v
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import audioshrink as a


class TestDetermineBitrate(unittest.TestCase):
    def test_default_bitrate(self):
        # Sample rate no longer affects bitrate; all music defaults to 96 kbps
        self.assertEqual(a.determine_bitrate({}), 96)
        self.assertEqual(a.determine_bitrate({"genre": "Rock"}), 96)
        self.assertEqual(a.determine_bitrate({"sample_rate": 96000}), 96)
        self.assertEqual(a.determine_bitrate({"sample_rate": 48000}), 96)
        self.assertEqual(a.determine_bitrate({"sample_rate": 0}), 96)

    def test_speech_caps_to_64(self):
        self.assertEqual(a.determine_bitrate({"genre": "Hörbuch"}), 64)
        self.assertEqual(a.determine_bitrate({"genre": "an Audiobook"}), 64)

    def test_classical_gets_112(self):
        self.assertEqual(a.determine_bitrate({"genre": "Klassik"}), 112)
        self.assertEqual(a.determine_bitrate({"genre": "Classical"}), 112)
        self.assertEqual(a.determine_bitrate({"genre": "classic"}), 112)

    def test_source_bitrate_cap(self):
        self.assertEqual(a.determine_bitrate({"bitrate_kbps": 80}), 80)
        self.assertEqual(a.determine_bitrate({"bitrate_kbps": 900}), 96)
        # Classical also capped at source bitrate
        self.assertEqual(a.determine_bitrate({"genre": "Klassik", "bitrate_kbps": 80}), 80)
        self.assertEqual(a.determine_bitrate({"genre": "Klassik", "bitrate_kbps": 900}), 112)


class TestIsSpeech(unittest.TestCase):
    def test_matches(self):
        for g in ("Hörbuch", "Some Audiobook", "PODCAST", "spoken word", "Hörspiel"):
            self.assertTrue(a.is_speech(g), g)

    def test_non_matches(self):
        for g in ("Rock", "", None, "Classical"):
            self.assertFalse(a.is_speech(g))


class TestIsClassical(unittest.TestCase):
    def test_matches(self):
        for g in ("Klassik", "classic", "Classical", "Neo-Classic"):
            self.assertTrue(a.is_classical(g), g)

    def test_non_matches(self):
        for g in ("Rock", "", None, "Hörbuch"):
            self.assertFalse(a.is_classical(g))


class TestShouldReencode(unittest.TestCase):
    def test_above_threshold(self):
        self.assertTrue(a.should_reencode({"bitrate_kbps": 321}, 192))
        self.assertTrue(a.should_reencode({"bitrate_kbps": 256}, 192))
        self.assertTrue(a.should_reencode({"bitrate_kbps": 245}, 192))

    def test_at_threshold_is_reencoded(self):
        # Threshold is inclusive (>=): at threshold → re-encode
        self.assertTrue(a.should_reencode({"bitrate_kbps": 192}, 192))

    def test_below_threshold(self):
        self.assertFalse(a.should_reencode({"bitrate_kbps": 128}, 192))
        self.assertFalse(a.should_reencode({"bitrate_kbps": 0}, 192))

    def test_genre_irrelevant_for_decision(self):
        # 320 kbps audiobook → re-encode (decision based only on bitrate) ...
        info_high = {"bitrate_kbps": 320, "genre": "Hörbuch"}
        self.assertTrue(a.should_reencode(info_high, 192))
        # ... but the TARGET bitrate remains genre-dependent (Speech → 64)
        self.assertEqual(a.determine_bitrate(info_high), 64)
        # 128 kbps audiobook below threshold → copy
        self.assertFalse(
            a.should_reencode({"bitrate_kbps": 128, "genre": "Hörbuch"}, 192))

    def test_default_threshold_320_inclusive(self):
        self.assertTrue(a.should_reencode({"bitrate_kbps": 400}))   # above → re-encode
        self.assertTrue(a.should_reencode({"bitrate_kbps": 320}))   # at default threshold → re-encode
        self.assertFalse(a.should_reencode({"bitrate_kbps": 319}))  # below → copy
        self.assertTrue(a.should_reencode({"bitrate_kbps": 160}, 128))  # custom threshold


class TestBuildMetadataOpts(unittest.TestCase):
    def test_full(self):
        info = {"tags": {"title": "T", "artist": "A", "album": "Alb",
                         "genre": "Rock", "date": "2020", "tracknumber": "3",
                         "comment": "hi"}}
        opts = a.build_metadata_opts(info)
        self.assertIn("--title", opts)
        self.assertIn("T", opts)
        self.assertIn("--artist", opts)
        self.assertIn("--tracknumber", opts)
        self.assertIn("3", opts)
        self.assertIn("--comment", opts)
        self.assertIn("comment=hi", opts)

    def test_track_alias(self):
        opts = a.build_metadata_opts({"tags": {"track": "5"}})
        self.assertIn("--tracknumber", opts)
        self.assertIn("5", opts)

    def test_empty(self):
        self.assertEqual(a.build_metadata_opts({"tags": {}}), [])
        self.assertEqual(a.build_metadata_opts({}), [])


class TestClassification(unittest.TestCase):
    def test_lossless(self):
        self.assertTrue(a.is_lossless(Path("x.FLAC")))
        self.assertTrue(a.is_lossless(Path("y.wav")))
        self.assertFalse(a.is_lossless(Path("z.mp3")))

    def test_ext_of(self):
        self.assertEqual(a.ext_of(Path("A.B.OpUs")), "opus")
        self.assertEqual(a.ext_of(Path("no_ext")), "")


class TestIsUpToDate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_target(self):
        src = self.d / "s.flac"
        src.write_bytes(b"12345")
        self.assertFalse(a.is_up_to_date(src, self.d / "d.opus", False))

    def test_equal_mtime_up_to_date(self):
        src = self.d / "s.flac"
        dst = self.d / "d.opus"
        src.write_bytes(b"12345")
        dst.write_bytes(b"x")
        os.utime(src, (1000, 1000))
        os.utime(dst, (1000, 1000))
        self.assertTrue(a.is_up_to_date(src, dst, compare_size=False))

    def test_source_newer_not_up_to_date(self):
        src = self.d / "s.flac"
        dst = self.d / "d.opus"
        src.write_bytes(b"x")
        dst.write_bytes(b"x")
        os.utime(dst, (1000, 1000))
        os.utime(src, (1100, 1100))  # 100 s newer
        self.assertFalse(a.is_up_to_date(src, dst, False))

    def test_copy_size_matters(self):
        src = self.d / "s.txt"
        dst = self.d / "d.txt"
        src.write_bytes(b"12345")
        dst.write_bytes(b"123")
        os.utime(src, (1000, 1000))
        os.utime(dst, (1000, 1000))
        self.assertFalse(a.is_up_to_date(src, dst, compare_size=True))
        self.assertTrue(a.is_up_to_date(src, dst, compare_size=False))


class TestSourceHasCounterpart(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.source = root / "src"
        self.target = root / "dst"
        self.source.mkdir()
        self.target.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _src(self, name):
        (self.source / name).write_bytes(b"x")

    def _tgt(self, name):
        p = self.target / name
        p.write_bytes(b"x")
        return p

    def test_opus_with_lossless_source(self):
        self._src("t.flac")
        tf = self._tgt("t.opus")
        self.assertTrue(a.source_has_counterpart(tf, self.source, self.target, False))

    def test_opus_orphan(self):
        tf = self._tgt("u.opus")
        self.assertFalse(a.source_has_counterpart(tf, self.source, self.target, False))

    def test_dotted_name(self):
        self._src("01. Track.flac")
        tf = self._tgt("01. Track.opus")
        self.assertTrue(a.source_has_counterpart(tf, self.source, self.target, False))

    def test_copy_with_source(self):
        self._src("c.jpg")
        cf = self._tgt("c.jpg")
        self.assertTrue(a.source_has_counterpart(cf, self.source, self.target, False))

    def test_copy_orphan(self):
        cf = self._tgt("d.lrc")
        self.assertFalse(a.source_has_counterpart(cf, self.source, self.target, False))

    def test_reencode_supersedes_old_mp3(self):
        self._src("song.mp3")
        tf = self._tgt("song.mp3")
        with mock.patch.object(a, "analyze_audio",
                               return_value={"bitrate_kbps": 400}):
            self.assertFalse(a.source_has_counterpart(tf, self.source, self.target, True))

    def test_reencode_keeps_opus(self):
        self._src("song.mp3")
        tf = self._tgt("song.opus")
        with mock.patch.object(a, "analyze_audio",
                               return_value={"bitrate_kbps": 400}):
            self.assertTrue(a.source_has_counterpart(tf, self.source, self.target, True))

    def test_opus_orphan_when_source_below_threshold(self):
        # song.mp3 (256k) with threshold 320 → copied, not re-encoded
        # → an existing song.opus (from a run with a lower threshold) is orphaned
        self._src("song.mp3")
        tf = self._tgt("song.opus")
        with mock.patch.object(a, "analyze_audio",
                               return_value={"bitrate_kbps": 256}):
            self.assertFalse(
                a.source_has_counterpart(tf, self.source, self.target, True, 320))

    def test_opus_valid_when_source_above_threshold(self):
        self._src("song.mp3")
        tf = self._tgt("song.opus")
        with mock.patch.object(a, "analyze_audio",
                               return_value={"bitrate_kbps": 400}):
            self.assertTrue(
                a.source_has_counterpart(tf, self.source, self.target, True, 320))

    def test_opus_valid_from_flac_regardless_of_lossy_sibling(self):
        # song.opus from song.flac → valid, even if song.mp3 (≤threshold) sits next to it
        self._src("song.flac")
        self._src("song.mp3")
        tf = self._tgt("song.opus")
        self.assertTrue(
            a.source_has_counterpart(tf, self.source, self.target, True, 320))

    def test_reencode_low_bitrate_keeps_mp3(self):
        self._src("song.mp3")
        tf = self._tgt("song.mp3")
        with mock.patch.object(a, "analyze_audio",
                               return_value={"bitrate_kbps": 64}):
            self.assertTrue(a.source_has_counterpart(tf, self.source, self.target, True))

    def test_no_reencode_keeps_mp3(self):
        self._src("song.mp3")
        tf = self._tgt("song.mp3")
        self.assertTrue(a.source_has_counterpart(tf, self.source, self.target, False))


class TestPlanAlbumCover(unittest.TestCase):
    def test_no_transcoded_tracks(self):
        # mp3 without re-encode → nothing transcoded → no dedup
        plan = a.plan_album_cover([Path("a.mp3"), Path("b.jpg")], False, 192)
        self.assertFalse(plan["discard_embedded"])
        self.assertIsNone(plan["write_cover"])

    def test_flac_existing_separate_cover(self):
        plan = a.plan_album_cover([Path("01.flac"), Path("cover.jpg")], False, 192)
        self.assertTrue(plan["discard_embedded"])
        self.assertIsNone(plan["write_cover"])

    def test_flac_same_embedded_cover(self):
        with mock.patch.object(a, "embedded_cover_bytes", return_value=b"\x89PNGsame"):
            plan = a.plan_album_cover([Path("01.flac"), Path("02.flac")], False, 192)
        self.assertTrue(plan["discard_embedded"])
        self.assertEqual(plan["write_cover"], b"\x89PNGsame")

    def test_flac_different_embedded_covers(self):
        with mock.patch.object(a, "embedded_cover_bytes", side_effect=[b"AAA", b"BBB"]):
            plan = a.plan_album_cover([Path("01.flac"), Path("02.flac")], False, 192)
        self.assertFalse(plan["discard_embedded"])
        self.assertIsNone(plan["write_cover"])

    def test_flac_missing_cover_no_dedup(self):
        # At least one track without cover → no deduplication
        with mock.patch.object(a, "embedded_cover_bytes", return_value=None):
            plan = a.plan_album_cover([Path("01.flac"), Path("02.flac")], False, 192)
        self.assertFalse(plan["discard_embedded"])
        self.assertIsNone(plan["write_cover"])


if __name__ == "__main__":
    unittest.main()
