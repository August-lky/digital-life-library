"""
2026 年热梗盘点（seeds/y2026）的测试。临时目录里跑，不联网。

  python -X utf8 -m unittest discover -s tests -v
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dlolib import Library  # noqa: E402
from dlolib.importers import chime, wikipedia, y2026, yearly  # noqa: E402


class Y2026Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = Path(tempfile.mkdtemp(prefix="dlolib-test-y2026-"))
        cls.lib = Library.init_home(cls.home, owner_name="老大")
        cls.book = cls.lib.book()
        for mod in (chime, wikipedia, yearly):
            mod.run(cls.book)
        cls.res = y2026.run(cls.book)
        cls.data = y2026.load()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home, ignore_errors=True)

    def test_import_adds_entries(self):
        self.assertTrue(self.res["ok"])
        self.assertEqual(self.res["created"] + len(self.res["skipped"]), len(self.data["terms"]))
        e = self.book.find_by_name("哭哭马")
        self.assertEqual(e["source"]["dataset"], "2026年盘点")
        self.assertEqual(e["source"]["license"], "CC0-1.0")
        self.assertEqual(e["source"]["by"], "import")
        self.assertTrue(e["refs"] and all(r["url"].startswith("http") for r in e["refs"]))

    def test_every_term_has_basics(self):
        for t in self.data["terms"]:
            self.assertTrue(t["name"] and t["brief"] and t["origin"] and t["era"], t["name"])
            self.assertLessEqual(len(t["brief"]), 80, t["name"])
            self.assertTrue(t["sources"], t["name"])
            self.assertIn("2026", t["era"] + t["origin"], t["name"])
            self.assertIn(t["confidence"], ("multi", "single"), t["name"])

    def test_chain_entries_have_prev_and_next(self):
        chains = [t for t in self.data["terms"] if t["type"] == "接龙梗"]
        self.assertGreaterEqual(len(chains), 2)
        for t in chains:
            self.assertTrue(t["prev"] and t["next"], t["name"])
            e = self.book.find_by_name(t["name"])
            self.assertEqual(e["next"], t["next"])
            self.assertEqual(e["prev"], t["prev"])

    def test_alias_lookup(self):
        hits = self.book.search("旧的我放转转上了啊")
        self.assertTrue(hits and hits[0]["entry"]["name"] == "那旧的呢")

    def test_reimport_is_stable(self):
        n = len(self.book.entries)
        y2026.run(self.book)
        self.assertEqual(len(self.book.entries), n)

    def test_existing_entry_only_gets_ref(self):
        e = self.book.find_by_name("爱你老己")
        self.assertEqual(e["source"]["dataset"], "Wikipedia")        # 没被抢走
        self.assertTrue(any(r.get("kind") == "y2026" for r in e["refs"]))
        y2026.run(self.book)
        e = self.book.find_by_name("爱你老己")
        self.assertEqual(sum(1 for r in e["refs"] if r.get("kind") == "y2026"), 2)   # 重导入不会越堆越多

    def test_no_politics_or_lyrics_markers(self):
        text = json.dumps(self.data, ensure_ascii=False)
        for word in ("命运共同体", "不忘初心", "新质生产力", "歌词："):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
