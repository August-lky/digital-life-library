"""
歌词梗白名单（seeds/lyric_memes_whitelist.json、dlolib/lyrics.py）的测试。临时目录里跑，不联网。

  python -X utf8 -m unittest discover -s tests -v
"""
import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dlolib import Library, lyrics, mcp_server, render  # noqa: E402
from dlolib.importers import jjk  # noqa: E402

REAL = json.loads((ROOT / "seeds" / "lyric_memes_whitelist.json").read_text(encoding="utf-8"))

# 测试专用的假白名单（假歌，假句子），不用真歌词
FAKE_SONG = {
    "id": "fake-song-2001", "title": "测试之歌", "artist": "某某", "year": 2001,
    "prev": "天上掉下一只猫", "next": "落在我家屋顶上", "variants": ["天上掉下一只狗"],
    "why": "测试用：这两句因为某类剪辑成了梗", "boundTo": "测试用的剪辑", "popularSince": "2026-01",
    "sources": ["https://example.org/a"],
}


def write_whitelist(directory: Path, songs: list) -> Path:
    p = directory / "wl.json"
    p.write_text(json.dumps({"songs": songs}, ensure_ascii=False), encoding="utf-8")
    return p


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlolib-test-lyrics-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.lib = Library.init_home(self.tmp / "home", owner_name="老大", agents={"fish": {"name": "小鲸鱼"}})
        self.book = self.lib.book()

    def use(self, songs):
        patcher = mock.patch.object(lyrics, "WHITELIST_PATH", write_whitelist(self.tmp, songs))
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def entry(book, name, **kw):
        return book.build_entry(name=name, brief=kw.pop("brief", "测试"),
                                source={"kind": "seed", "by": "import", "dataset": "t", "license": "CC0-1.0"}, **kw)


class WhitelistFileTests(Base):
    def test_shipped_whitelist_is_valid(self):
        wl = lyrics.load()
        self.assertEqual(wl.problems, [])
        song = wl.by_id["yuai-2010"]
        self.assertEqual((song.title, song.artist, song.year), ("雨爱", "杨丞琳", 2010))
        self.assertEqual((song.prev, song.next), ("听雨的声音", "一滴滴清晰"))
        for s in wl.songs:
            self.assertLessEqual(len(s.lines), 2)
            self.assertTrue(all(len(x) <= lyrics.MAX_LINE_CHARS for x in s.phrases()))
            self.assertTrue(s.why and s.bound_to and s.popular_since and s.sources)

    def test_validation_rules(self):
        def bad(**change):
            row = {**copy.deepcopy(FAKE_SONG), **change}
            songs, problems = lyrics.validate({"songs": [row]})
            self.assertEqual(songs, [], change)
            self.assertTrue(problems, change)

        songs, problems = lyrics.validate({"songs": [FAKE_SONG]})
        self.assertEqual((len(songs), problems), (1, []))
        bad(prev="这一句有二十一个字这一句有二十一个字这一句有二十一")             # 单句 > 20 字
        bad(third="第三句")                                                      # 不收第三句
        bad(variants=["完全不一样的另一句歌词"])                                   # 变体不能夹带别的句子
        bad(variants=["天上掉下一只猫a", "天上掉下一只猫b", "天上掉下一只猫c", "天上掉下一只猫d"])
        bad(sources=[])
        bad(why="")
        bad(popularSince="去年")
        bad(prev="第一行\n第二行")

    def test_missing_or_broken_file_means_empty_whitelist(self):
        with mock.patch.object(lyrics, "WHITELIST_PATH", self.tmp / "nope.json"):
            self.assertEqual(lyrics.load().songs, [])
        bad = self.tmp / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with mock.patch.object(lyrics, "WHITELIST_PATH", bad):
            self.assertEqual(lyrics.load().songs, [])


class RainLoveTests(Base):
    """真实白名单里的《雨爱》：能收、能查、能接下句。"""

    def setUp(self):
        super().setUp()
        self.res = jjk.run(self.book)

    def test_imported_as_lyric_meme(self):
        self.assertEqual((self.res["rejected"], self.res["skipped"]), ([], []))
        e = self.book.find_by_name("听雨的声音")
        self.assertEqual(e["type"], "歌词梗")
        self.assertEqual(e["lyric"], {"id": "yuai-2010", "song": "雨爱", "artist": "杨丞琳", "year": 2010})
        self.assertEqual((e["prev"], e["next"]), ("听雨的声音", "一滴滴清晰"))
        self.assertIn("这句本身是歌词", e["meaning"])
        self.assertIn("DJ", e["origin"])

    def test_findable_by_either_line_and_variant(self):
        for q in ("听雨的声音", "一滴滴清晰", "一点点清晰", "听雨的声音一滴滴清晰"):
            hits = self.book.search(q)
            self.assertTrue(hits and hits[0]["entry"]["name"] == "听雨的声音", q)
        e = self.book.search("听雨的声音")[0]["entry"]
        text = render.render_entry(self.lib, e)
        self.assertIn("接龙：听雨的声音 → 一滴滴清晰", text)
        self.assertIn("杨丞琳《雨爱》", text)

    def test_only_two_lines_are_stored(self):
        e = self.book.find_by_name("听雨的声音")
        blob = json.dumps(e, ensure_ascii=False)
        self.assertNotIn("你的呼吸", blob)
        for r in self.book.entries:
            self.assertFalse(lyrics.looks_like_lyrics(r.get("origin")), r["name"])
            self.assertFalse(lyrics.looks_like_lyrics(r.get("meaning")), r["name"])

    def test_extra_alias_or_third_line_rejected(self):
        e = self.book.find_by_name("听雨的声音")
        for alias in ("听雨的声音一滴滴清晰第三句", "另一句歌词"):
            res = self.book.update(e["id"], {"aliases": [*e["aliases"], alias]}, "owner")
            self.assertFalse(res["ok"], alias)
            self.assertIn("白名单", res["error"])
        self.assertEqual(self.book.find_by_name("听雨的声音")["aliases"], e["aliases"])

    def test_cannot_rename_or_retype_lyric_meme(self):
        e = self.book.find_by_name("听雨的声音")
        self.assertFalse(self.book.update(e["id"], {"name": "你的呼吸渗入"}, "owner")["ok"])
        res = self.book.update(e["id"], {"type": "台词梗"}, "owner")
        self.assertFalse(res["ok"])
        # 标过时、隐藏这类不碰歌词的操作不受影响
        self.assertTrue(self.book.mark(e["id"], "owner", status="dated")["ok"])

    def test_reimport_stable(self):
        n = len(self.book.entries)
        self.assertEqual(jjk.run(self.book)["rejected"], [])
        self.assertEqual(len(self.book.entries), n)


class StoreLayerTests(Base):
    """校验在 store 层：add / update / import_entries 谁都绕不过。用假白名单。"""

    def setUp(self):
        super().setUp()
        self.use([FAKE_SONG])

    def test_whitelisted_can_be_added_and_chains(self):
        res = self.book.add(name="天上掉下一只猫", brief="测试用的歌词梗", source={"kind": "owner"},
                            aliases=["天上掉下一只狗"], type="歌词梗")
        self.assertTrue(res["ok"], res)
        e = res["entry"]
        self.assertEqual((e["type"], e["prev"], e["next"], e["lyric"]["song"]),
                         ("歌词梗", "天上掉下一只猫", "落在我家屋顶上", "测试之歌"))
        for q in ("落在我家屋顶上", "天上掉下一只狗"):
            self.assertEqual(self.book.search(q)[0]["entry"]["name"], "天上掉下一只猫")

    def test_name_alone_marks_it_as_lyric_meme(self):
        res = self.book.add(name="落在我家屋顶上", brief="测试", source={"kind": "owner"})   # 没写 type
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["entry"]["type"], "歌词梗")

    def test_not_in_whitelist_rejected(self):
        for kw in ({"type": "歌词梗"}, {"type": "歌词梗", "aliases": ["天上掉下一只猫"]}):
            res = self.book.add(name="并不在白名单里的一句", brief="测试", source={"kind": "owner"}, **kw)
            self.assertFalse(res["ok"], kw)
            self.assertIn("歌词白名单", res["error"])
        self.assertIsNone(self.book.find_by_name("并不在白名单里的一句"))

    def test_alias_outside_whitelist_rejected(self):
        res = self.book.add(name="天上掉下一只猫", brief="测试", source={"kind": "owner"}, aliases=["第三句歌词"])
        self.assertFalse(res["ok"])
        self.assertIn("白名单", res["error"])

    def test_import_rejects_bad_lyric_entries(self):
        good = self.entry(self.book, "天上掉下一只猫", type="歌词梗", aliases=["落在我家屋顶上"])
        undeclared_third = self.entry(self.book, "天上掉下一只猫", type="歌词梗", aliases=["第三句歌词"])
        wrong = self.entry(self.book, "没登记的一句", type="歌词梗")
        wrong_next = self.entry(self.book, "落在我家屋顶上", type="歌词梗")
        wrong_next["next"] = "别的一句"
        for e in (undeclared_third, wrong, wrong_next):
            res = self.book.import_entries([e], "t")
            self.assertEqual((res["added"], len(res["rejected"])), (0, 1), e["name"])
        res = self.book.import_entries([good], "t")
        self.assertEqual((res["added"], res["rejected"]), (1, []))
        self.assertEqual(self.book.find_by_name("天上掉下一只猫")["lyric"]["id"], "fake-song-2001")

    def test_without_whitelist_nothing_lyric_is_accepted(self):
        with mock.patch.object(lyrics, "WHITELIST_PATH", self.tmp / "nope.json"):
            res = self.book.add(name="天上掉下一只猫", brief="测试", source={"kind": "owner"}, type="歌词梗")
            self.assertFalse(res["ok"])

    def test_long_line_whitelist_entry_is_dropped(self):
        long_song = {**FAKE_SONG, "id": "long-song", "prev": "这一句有二十一个字这一句有二十一个字这一句有二十一"}
        self.use([long_song])
        self.assertEqual(lyrics.load().songs, [])
        res = self.book.add(name=long_song["prev"], brief="测试", source={"kind": "owner"}, type="歌词梗")
        self.assertFalse(res["ok"])


class LyricDumpTests(Base):
    """她们 / 主人存条目时抄进大段歌词：拦住那部分，家常提示。"""

    DUMP = "第一行测试歌词在这里\n第二行测试歌词在这里\n第三行测试歌词在这里\n第四行测试歌词在这里"

    def test_dump_in_origin_is_dropped_rest_saved(self):
        res = self.book.add(name="某个梗", brief="一句话意思", origin=self.DUMP, source={"kind": "owner"})
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["entry"]["origin"], "")
        self.assertEqual(res["dropped"], ["origin"])
        self.assertIn("歌词", res["note"])
        self.assertNotIn("第三行", json.dumps(res["entry"], ensure_ascii=False))

    def test_long_quote_after_lyric_word_is_redacted(self):
        origin = "出自某歌里的歌词：“这是一整句相当长的歌词不止二十个字啊这是第二小句也很长很长很长”，后来被大家拿来玩。"
        res = self.book.add(name="某个梗", brief="一句话意思", origin=origin, source={"kind": "owner"},
                            examples=[self.DUMP, "正常的例句"])
        self.assertTrue(res["ok"], res)
        self.assertIn("（歌词略）", res["entry"]["origin"])
        self.assertIn("后来被大家拿来玩", res["entry"]["origin"])
        self.assertNotIn("相当长", res["entry"]["origin"])
        self.assertEqual(res["entry"]["examples"], ["正常的例句"])

    def test_dump_in_brief_or_name_rejected(self):
        res = self.book.add(name="某个梗", brief=self.DUMP[:75].replace("\n", "\n"), source={"kind": "owner"})
        self.assertFalse(res["ok"])
        self.assertIn("歌词不收", res["error"])
        res = self.book.add(name="某个梗", brief="意思", aliases=[self.DUMP], source={"kind": "owner"})
        self.assertFalse(res["ok"])

    def test_update_drops_lyric_part_only(self):
        e = self.book.add(name="某个梗", brief="一句话意思", source={"kind": "owner"})["entry"]
        res = self.book.update(e["id"], {"origin": self.DUMP, "era": "2026年"}, "owner")
        self.assertTrue(res["ok"])
        self.assertEqual((res["entry"]["era"], res["entry"]["origin"]), ("2026年", ""))
        self.assertIn("歌词", res["note"])

    def test_ordinary_text_not_flagged(self):
        ok = ["也叫 yyds / 永远的神 / 绝绝子", "通常金发与巨乳配合/女二号人气往往不逊于/女主角，于是有了这一定理",
              "A：你吃了吗\nB：吃了，你呢\nA：还没，一起去吧", "出自某歌的歌词：“人生已经如此的艰难”，被广泛应用于调侃。",
              "他说：“每次听到这句歌词，我就不自觉想到被绿的梗，真的太好笑了”", ""]
        for t in ok:
            self.assertFalse(lyrics.looks_like_lyrics(t), t)

    def test_colon_form_redacts_only_the_run(self):
        t = "这句来自一首歌，歌词：这是一整串相当长的测试歌词不止二十个字啊真的很长很长。后来被拿来玩梗。"
        out, changed = lyrics.redact(t)
        self.assertTrue(changed)
        self.assertIn("后来被拿来玩梗", out)
        self.assertNotIn("相当长", out)
        quoted = "出自林某某的歌，歌词：“人生已经如此的艰难，有些事情就不要拆穿。” 被广泛应用于调侃。"
        self.assertEqual(lyrics.redact(quoted), (quoted, False))        # 一句的量，且后文不受影响

    def test_whitelisted_lines_do_not_count_as_dump(self):
        t = "歌词是“听雨的声音，一滴滴清晰”，出自《雨爱》\n听雨的声音\n一滴滴清晰\n听雨的声音"
        self.assertFalse(lyrics.looks_like_lyrics(t))

    def test_import_trims_but_keeps_entry(self):
        e = self.entry(self.book, "某个梗", origin=self.DUMP)
        res = self.book.import_entries([e], "t")
        self.assertEqual((res["added"], res["rejected"]), (1, []))
        self.assertEqual(res["trimmed"], [{"name": "某个梗", "fields": ["origin"]}])
        self.assertEqual(self.book.find_by_name("某个梗")["origin"], "")

    def test_mcp_save_gentle_hint(self):
        h = mcp_server.Handler(self.lib, "fish")
        call = lambda **a: h.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                     "params": {"name": "library_save", "arguments": a}})["result"]["content"][0]["text"]
        text = call(name="某个梗", brief="一句话意思", where="某个群", origin=self.DUMP)
        self.assertIn("存好了", text)
        self.assertIn("歌词不收", text)
        self.assertEqual(self.book.find_by_name("某个梗")["origin"], "")
        text = call(name="另一个梗", brief=self.DUMP, where="某个群")
        self.assertIn("没存成", text)
        self.assertIn("歌词不收", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
