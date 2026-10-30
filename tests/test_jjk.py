"""
咒术回战梗（seeds/jjk）的测试。临时目录里跑，不联网。

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
from dlolib.importers import chime, jjk, wikipedia, y2026, yearly  # noqa: E402


class JjkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = Path(tempfile.mkdtemp(prefix="dlolib-test-jjk-"))
        cls.lib = Library.init_home(cls.home, owner_name="老大")
        cls.book = cls.lib.book()
        for mod in (chime, wikipedia, yearly, y2026):
            mod.run(cls.book)
        cls.res = jjk.run(cls.book)
        cls.data = jjk.load()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home, ignore_errors=True)

    def test_import_adds_entries(self):
        self.assertTrue(self.res["ok"])
        self.assertEqual(self.res["created"] + len(self.res["skipped"]), len(self.data["terms"]))
        self.assertEqual(self.res["skipped"], [])          # 没有和书里别的来源撞名
        e = self.book.find_by_name("会赢的")
        self.assertEqual(e["source"]["dataset"], "咒术回战梗")
        self.assertEqual(e["source"]["license"], "CC0-1.0")
        self.assertEqual(e["source"]["by"], "import")
        self.assertTrue(e["refs"] and all(r["url"].startswith("http") for r in e["refs"]))
        self.assertTrue(all(r["kind"] == "jjk" for r in e["refs"]))

    def test_every_term_has_basics(self):
        names = set()
        for t in self.data["terms"]:
            self.assertTrue(t["name"] and t["brief"] and t["origin"] and t["era"] and t["meaning"], t["name"])
            self.assertLessEqual(len(t["brief"]), 80, t["name"])
            self.assertTrue(t["sources"], t["name"])
            self.assertIn(t["confidence"], ("multi", "single"), t["name"])
            self.assertIn(t["year"], range(2018, 2027), t["name"])
            self.assertNotIn(t["name"], names)
            names.add(t["name"])
            for u in t["sources"]:
                self.assertTrue(u.startswith("https://"), t["name"])

    def test_chain_entries_have_prev_and_next(self):
        chains = [t for t in self.data["terms"] if t["type"] == "接龙梗"]
        self.assertGreaterEqual(len(chains), 3)
        for t in chains:
            self.assertTrue(t["prev"] and t["next"], t["name"])
            e = self.book.find_by_name(t["name"])
            self.assertEqual(e["prev"], t["prev"])
            self.assertEqual(e["next"], t["next"])

    def test_rain_love_chain_is_findable(self):
        # 听到上句要能接下句：上句、下句都能查到
        e = self.book.find_by_name("听雨的声音")
        self.assertEqual(e["type"], "接龙梗")
        self.assertEqual(e["next"], "一滴滴清晰")
        hits = self.book.search("一滴滴清晰")
        self.assertTrue(hits and hits[0]["entry"]["name"] == "听雨的声音")
        # 这条是歌里的一句：只留作梗名的短语，不能把整段歌词存进来
        text = json.dumps(e, ensure_ascii=False)
        self.assertNotIn("你的呼吸", text)

    def test_win_chain_pairs(self):
        e = self.book.find_by_name("会赢的")
        self.assertEqual(e["next"], "对不起，没能让你使出全力")
        self.assertTrue(self.book.find_by_name("你才是挑战者"))
        self.assertTrue(self.book.find_by_name("2.5条悟"))

    def test_lookup_by_alias_and_core_names(self):
        for q, name in (("五条悟一分为二", "2.5条悟"), ("宿傩", "两面宿傩"), ("咒回", "咒术回战"),
                        ("惠吟的", "会赢的"), ("机甲番", "乙骨驾驶五条悟")):
            hits = self.book.search(q)
            self.assertTrue(hits and hits[0]["entry"]["name"] == name, q)

    def test_reimport_is_stable(self):
        n = len(self.book.entries)
        jjk.run(self.book)
        self.assertEqual(len(self.book.entries), n)
        y2026.run(self.book)                                 # 两个数据集互不冲掉
        self.assertEqual(len(self.book.entries), n)

    def test_not_jjk_memes_live_in_2026(self):
        # 「你只是怕了 / 这谁不怕」查下来和咒术回战无关，放在 2026 盘点里；这里不应再出现
        names = {t["name"] for t in self.data["terms"]}
        self.assertNotIn("你只是怕了", names)
        self.assertNotIn("这谁不怕", names)
        self.assertEqual(self.book.find_by_name("你只是怕了")["source"]["dataset"], "2026年盘点")

    def test_no_long_quotes(self):
        # 释义自己写：不收整段台词 / 歌词。单条文字不长，也不出现歌词整句
        for t in self.data["terms"]:
            self.assertLessEqual(len(t["meaning"]), 260, t["name"])
            self.assertLessEqual(len(t["origin"]), 260, t["name"])
        text = json.dumps(self.data, ensure_ascii=False)
        for frag in ("渗入我的爱里", "歌词："):
            self.assertNotIn(frag, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
