"""
核心测试。全部在临时目录里跑，不碰任何正式的图书馆目录，不联网。

  python -X utf8 -m unittest discover -s tests -v
"""
import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dlolib import Library, norm, phonetic  # noqa: E402
from dlolib import export, mcp_server, render, server  # noqa: E402
from dlolib.importers import chime, wikipedia, yearly  # noqa: E402
from dlolib.store import locked  # noqa: E402

PYTHON = sys.executable


def make_home(seeds=("chime", "wikipedia", "yearly"), agents=True) -> Library:
    home = Path(tempfile.mkdtemp(prefix="dlolib-test-"))
    lib = Library.init_home(home, owner_name="老大",
                            agents={"fish": {"name": "小鲸鱼"}, "sister": {"name": "姐姐"}} if agents else None)
    book = lib.book()
    for s in seeds:
        {"chime": chime, "wikipedia": wikipedia, "yearly": yearly}[s].run(book)
    return lib


class Base(unittest.TestCase):
    homes = []

    @classmethod
    def tearDownClass(cls):
        for h in cls.homes:
            shutil.rmtree(h, ignore_errors=True)

    @classmethod
    def new_home(cls, **kw):
        lib = make_home(**kw)
        cls.homes.append(lib.home)
        return lib


# ───────────────────────── 检索 ─────────────────────────

class SearchTests(Base):
    @classmethod
    def setUpClass(cls):
        cls.lib = cls.new_home()
        cls.book = cls.lib.book()
        cls._saved = phonetic._STATE.copy()
        phonetic.set_converter(None)        # 这一组测的是不依赖拼音库的基本检索
        cls.book.reload()

    @classmethod
    def tearDownClass(cls):
        phonetic._STATE.update(cls._saved)
        super().tearDownClass()

    def top(self, q, k=3):
        return [(r["entry"]["name"], r["score"], r["why"]) for r in self.book.search(q, k=k)]

    def test_exact_name(self):
        for q, name in [("city不city", "city不city"), ("yyds", "yyds"), ("尊嘟假嘟", "尊嘟假嘟")]:
            top = self.top(q)
            self.assertEqual(top[0][0], name)
            self.assertEqual(top[0][1], 1.0)

    def test_case_punct_repeat(self):
        self.assertEqual(self.top("YYDS！！！")[0][0], "yyds")
        self.assertEqual(self.top("绝绝绝绝子")[0][0], "绝绝子")

    def test_name_inside_sentence(self):
        top = self.top("这家店也太city了吧")
        self.assertEqual(top[0][0], "city不city")

    def test_origin_text(self):
        self.assertEqual(self.top("妹妹长城美不美")[0][0], "city不city")

    def test_one_char_variant(self):
        self.assertEqual(self.top("永远滴神")[0][0], "yyds")      # 靠别名「永远的神」
        self.assertEqual(self.top("泰酷辣")[0][0], "泰裤辣")

    def test_flagged_entry_is_marked(self):
        e = self.book.search("你这是依托答辩")[0]["entry"]
        self.assertEqual(e["name"], "依托答辩")
        self.assertTrue(e["flags"].get("profanity"))
        self.assertIn("带脏字", render.render_entry(self.lib, e))

    def test_short_phrase_not_flooded(self):
        for q in ("好的", "今天吃什么", "我好累啊"):
            hits = self.top(q)
            self.assertTrue(all(s < 0.62 for _, s, _ in hits), (q, hits))

    def test_unknown_things_not_found(self):
        self.assertEqual(self.book.search("蓝色大肥鱼"), [])
        self.assertIn("书里没找到", render.render_search(self.lib, "蓝色大肥鱼", []))
        self.assertEqual(self.book.search("听雨的声音一点点清晰"), [])    # 歌词不收

    def test_hidden_not_returned(self):
        e = self.book.find_by_name("鼠鼠")
        self.book.mark(e["id"], "owner", hidden=True)
        try:
            self.assertNotIn("鼠鼠", [n for n, _, _ in self.top("鼠鼠")])
            self.assertIn("鼠鼠", [r["entry"]["name"] for r in self.book.search("鼠鼠", include_hidden=True)])
        finally:
            self.book.mark(e["id"], "owner", hidden=False)

    def test_scan_rules(self):
        names = [e["name"] for e in self.book.scan("今晚去吃泰裤辣的那家店")]
        self.assertIn("泰裤辣", names)
        self.assertEqual(self.book.scan("今天天气不错"), [])
        # 冒犯类不主动提示，过时的也不
        self.assertEqual([e for e in self.book.scan("你这是依托答辩") if e["name"] == "依托答辩"], [])
        e = self.book.find_by_name("city不city")
        self.book.mark(e["id"], "owner", status="dated")
        try:
            self.assertEqual([x for x in self.book.scan("这也太city不city了") if x["name"] == "city不city"], [])
        finally:
            self.book.mark(e["id"], "owner", status="unknown")

    def test_scan_limit(self):
        hits = self.book.scan("泰裤辣 yyds 尊嘟假嘟 city不city")
        self.assertLessEqual(len(hits), 2)


# ───────────────────────── 谐音钩子 ─────────────────────────

class PhoneticTests(Base):
    def tearDown(self):
        phonetic.reset()

    def test_works_without_pinyin_library(self):
        phonetic.set_converter(None)
        lib = self.new_home(seeds=("chime",))
        self.assertFalse(phonetic.available())
        self.assertTrue(lib.book().search("尊嘟假嘟"))          # 照常能查，只是少了谐音这一路

    def test_stub_converter_adds_homophone_match(self):
        table = {"真": "zhen", "的": "de", "假": "jia", "尊": "zun", "嘟": "du", "太": "tai", "酷": "ku", "啦": "la",
                 "泰": "tai", "裤": "ku", "辣": "la"}
        phonetic.set_converter(lambda t: [table.get(c, c) for c in t])
        lib = self.new_home(seeds=("chime",))
        res = lib.book().search("真的假的")
        self.assertEqual(res[0]["entry"]["name"], "尊嘟假嘟")
        self.assertIn("读音", res[0]["why"])
        res = lib.book().search("太酷啦")
        self.assertEqual(res[0]["entry"]["name"], "泰裤辣")

    def test_no_false_homophone_for_two_char_words(self):
        phonetic.set_converter(lambda t: ["shi", "jie"][: len(t)])
        self.assertIsNone(phonetic.phonetic_score(["shi", "jie"], ["shi", "jie"]))

    @unittest.skipUnless(phonetic.available(), "本机没装 pypinyin")
    def test_real_pypinyin(self):
        phonetic.reset()
        lib = self.new_home(seeds=("chime",))
        res = lib.book().search("真的假的")
        self.assertEqual(res[0]["entry"]["name"], "尊嘟假嘟")
        res = lib.book().search("太酷啦")
        self.assertEqual(res[0]["entry"]["name"], "泰裤辣")
        self.assertEqual(lib.book().search("好的")[:1] and [r["entry"]["name"] for r in lib.book().search("好的")], [])


# ───────────────────────── 来源校验 / 隐私 / 增删改 ─────────────────────────

class WriteTests(Base):
    def setUp(self):
        self.lib = self.new_home(seeds=())
        self.book = self.lib.book()

    def add(self, **kw):
        base = dict(name="测试梗", brief="一句意思", source={"kind": "self", "by": "fish", "where": "某个群"})
        base.update(kw)
        return self.book.add(**base)

    def test_four_source_kinds(self):
        ok = [
            {"kind": "self", "by": "fish", "where": "某个群"},
            {"kind": "asked", "by": "sister", "url": "https://example.org/x"},
            {"kind": "asked", "by": "fish", "note": "搜到的一句话"},
            {"kind": "owner"},
            {"kind": "seed", "dataset": "X", "license": "MIT"},
        ]
        for i, src in enumerate(ok):
            r = self.add(name=f"梗甲{i}号", source=src)
            self.assertTrue(r["ok"], (src, r))
        by = {e["name"]: e["source"] for e in self.book.entries}
        self.assertEqual(by["梗甲3号"]["by"], "owner")
        self.assertEqual(by["梗甲4号"]["by"], "import")

    def test_bad_sources_rejected(self):
        bad = [
            {"kind": "weird"},
            {"kind": "self", "by": "fish"},                                  # 缺 where
            {"kind": "self", "where": "群"},                                  # 缺 by
            {"kind": "self", "by": "stranger", "where": "群"},                # 没登记的人
            {"kind": "asked", "by": "fish"},                                  # 缺 url / note
            {"kind": "seed", "dataset": "X"},                                 # 缺 license
        ]
        for i, src in enumerate(bad):
            r = self.add(name=f"坏梗{i}号", source=src)
            self.assertFalse(r["ok"], src)
        self.assertEqual(len(self.book.entries), 0)

    def test_owner_cannot_be_forged_by_by_field(self):
        r = self.add(name="冒名", source={"kind": "owner", "by": "fish"})
        self.assertEqual(r["entry"]["source"]["by"], "owner")        # 手填的 by 永远是 owner

    def test_privacy_blocked(self):
        cases = [
            dict(brief="打我电话 13812345678"), dict(origin="找他 abc@example.com"),
            dict(examples=["我的QQ 12345678"]), dict(brief="身份证 110101199001011234"),
            dict(aliases=["微信号 wxid_abcdef"]),
        ]
        for i, c in enumerate(cases):
            r = self.add(name=f"带号码{i}", **c)
            self.assertFalse(r["ok"], c)
            self.assertIn("个人信息", r["error"])
        self.assertTrue(self.add(name="正常梗", brief="发布于2024年的一个梗")["ok"])
        r = self.book.update(self.book.find_by_name("正常梗")["id"], {"brief": "加我微信 abcdef123"}, "owner")
        self.assertFalse(r["ok"])

    def test_limits_and_duplicates(self):
        self.assertFalse(self.add(name="")["ok"])
        self.assertFalse(self.add(name="长" * 31)["ok"])
        self.assertFalse(self.add(brief="长" * 81)["ok"])
        self.assertTrue(self.add(name="蓝色大肥鱼", aliases=["大肥鱼啊"])["ok"])
        r = self.add(name="蓝色 大肥鱼")                       # 归一后同名
        self.assertFalse(r["ok"])
        self.assertFalse(self.add(name="别的名字", aliases=["大肥鱼啊"])["ok"])

    def test_update_history_and_mark(self):
        e = self.add(name="历史梗")["entry"]
        r = self.book.update(e["id"], {"brief": "新的意思", "era": "2026年"}, "owner")
        self.assertTrue(r["ok"])
        h = r["entry"]["history"][-1]
        self.assertEqual((h["by"], h["op"], h["fields"]), ("owner", "update", ["brief", "era"]))
        self.assertEqual(h["before"]["brief"], "一句意思")
        self.assertGreater(r["entry"]["updatedAt"], "") and self.assertEqual(r["entry"]["createdAt"], e["createdAt"])
        self.assertFalse(self.book.update(e["id"], {"source": {}}, "owner")["ok"])           # 来源不能改
        self.assertFalse(self.book.update(e["id"], {"status": "bogus"}, "owner")["ok"])
        self.assertTrue(self.book.mark(e["id"], "fish", status="dated")["ok"])
        self.assertEqual(self.book.get(e["id"])["status"], "dated")
        for i in range(30):
            self.book.update(e["id"], {"heat": float(i)}, "owner")
        self.assertLessEqual(len(self.book.get(e["id"])["history"]), 20)

    def test_remove_keeps_a_copy(self):
        e = self.add(name="要删的梗")["entry"]
        self.assertTrue(self.book.remove(e["id"], "owner")["ok"])
        self.assertIsNone(self.book.get(e["id"]))
        gone = (self.book.dir / "removed.jsonl").read_text(encoding="utf-8")
        self.assertIn("要删的梗", gone)

    def test_reload_sees_other_writers(self):
        other = Library(self.lib.home).book()
        other.add(name="别人加的", brief="意思", source={"kind": "owner"})
        self.assertIsNotNone(self.lib.book().find_by_name("别人加的"))


class GrowthFlowTests(Base):
    def test_blue_fish_flow(self):
        lib = self.new_home()
        book = lib.book()
        self.assertEqual(book.search("蓝色大肥鱼"), [])
        r = book.add(name="蓝色大肥鱼", brief="DeepSeek 鲸鱼形象的外号，群友这样叫它",
                     source={"kind": "self", "by": "fish", "where": "测试群"})
        self.assertTrue(r["ok"])
        hit = book.search("蓝色大肥鱼")[0]
        self.assertEqual((hit["entry"]["name"], hit["score"]), ("蓝色大肥鱼", 1.0))
        self.assertIn("小鲸鱼存的", render.render_entry(lib, hit["entry"]))
        self.assertEqual(book.search("大肥鱼")[0]["entry"]["name"], "蓝色大肥鱼")
        book.mark(r["entry"]["id"], "fish", status="dated")
        self.assertEqual(book.scan("蓝色大肥鱼来了"), [])


class ConcurrencyTests(Base):
    def test_many_processes_write_without_losing_entries(self):
        lib = self.new_home(seeds=())
        code = (
            "import sys; sys.path.insert(0, sys.argv[1])\n"
            "from dlolib import Library\n"
            "b = Library(sys.argv[2]).book()\n"
            "for i in range(8):\n"
            "    r = b.add(name='并发梗%s号%s' % (sys.argv[3], i), brief='x', source={'kind': 'owner'})\n"
            "    assert r['ok'], r\n"
        )
        procs = [subprocess.Popen([PYTHON, "-I", "-c", code, str(ROOT), str(lib.home), str(n)]) for n in range(6)]
        self.assertTrue(all(p.wait(60) == 0 for p in procs))
        book = Library(lib.home).book()
        self.assertEqual(len(book.entries), 48)
        self.assertEqual(len({e["id"] for e in book.entries}), 48)
        raw = (book.dir / "entries.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(raw), 48)
        for line in raw:
            json.loads(line)                                    # 没有写坏的行
        self.assertFalse(list(book.dir.glob("*.lock")))

    def test_stale_lock_is_cleared_and_live_lock_times_out(self):
        d = Path(tempfile.mkdtemp(prefix="dlolib-lock-"))
        self.homes.append(d)
        target = d / "x.jsonl"
        with locked(target):
            with self.assertRaises(TimeoutError):
                with locked(target, timeout=0.3):
                    pass
        lock = Path(str(target) + ".lock")
        lock.write_text("")
        old = lock.stat().st_mtime - 120
        os.utime(lock, (old, old))
        with locked(target, timeout=1):
            pass


# ───────────────────────── 导入器 ─────────────────────────

class ImporterTests(Base):
    def test_chime_import_complete_and_idempotent(self):
        lib = self.new_home(seeds=())
        book = lib.book()
        r1 = chime.run(book)
        raw = chime.load_raw()
        self.assertEqual(r1["source"], len(raw))
        self.assertEqual(len(book.entries), len(raw) - r1["mergedDuplicates"])
        self.assertTrue(all(e["source"]["by"] == "import" and "who" not in e["source"] for e in book.entries))
        self.assertTrue(all(e["source"]["license"] == "MIT" for e in book.entries))
        self.assertTrue(all(len(e["brief"]) <= 80 for e in book.entries))
        self.assertEqual(sum(1 for e in book.entries if e["hidden"]), 0)               # 冒犯类只打标记，不隐藏
        self.assertGreater(sum(1 for e in book.entries if e["flags"]), 100)
        n = len(book.entries)
        r2 = chime.run(book)
        self.assertEqual(len(book.entries), n)
        self.assertEqual(r2["replaced"], n)

    def test_reimport_keeps_human_edits(self):
        lib = self.new_home(seeds=("chime",))
        book = lib.book()
        e = book.find_by_name("鼠鼠")
        book.update(e["id"], {"brief": "我自己写的意思"}, "owner")
        chime.run(book)
        self.assertEqual(book.find_by_name("鼠鼠")["brief"], "我自己写的意思")

    def test_wikipedia_parse_and_filters(self):
        html = (
            "<h2 id='x'>形式</h2><h3>谐音</h3><ul>"
            "<li><b>杯具</b>：“悲剧”的谐音<sup>[1]</sup></li>"
            "<li><b>测试词甲</b>：一个说明</li></ul>"
            "<h2>政治类</h2><ul><li><b>某政治词</b>：说明</li></ul>"
            "<h2>体育运动类</h2><ul><li><b>某球员</b>：说明</li></ul>"
            "<h2>日常生活类</h2><h3>口头禅</h3><ul><li><b>测试词乙</b>：说明<ul><li><b>嵌套的</b>：不要</li></ul></li></ul>"
        )
        items = wikipedia.parse_html(html)
        names = [i["name"] for i in items]
        self.assertEqual(names, ["杯具", "测试词甲", "某政治词", "某球员", "测试词乙"])
        raw = {"page": "p", "url": "u", "revid": 1, "retrieved": "2026-10-30", "variant": "zh-cn", "items": items}
        kept = [i["name"] for i in wikipedia.candidates(raw)]
        self.assertEqual(kept, ["杯具", "测试词甲", "测试词乙"])
        self.assertEqual([i["name"] for i in wikipedia.candidates(raw, {norm("杯具")})], ["测试词甲", "测试词乙"])
        seed = wikipedia.build_seed(raw, {"测试词甲": "我写的释义", "测试词乙": {"brief": "另一个", "flags": {"offense": True}}})
        self.assertEqual([t["name"] for t in seed["terms"]], ["测试词甲", "测试词乙"])      # 没写释义的不收
        self.assertIn("CC BY-SA 4.0", seed["attribution"])

    def test_wikipedia_split_names(self):
        self.assertEqual(wikipedia.split_names("吸猫、吸狗、吸兔"), ("吸猫", ["吸狗", "吸兔"]))
        self.assertEqual(wikipedia.split_names("二/2"), ("二/2", []))

    def test_wikipedia_seed_file_is_clean(self):
        seed = json.loads(wikipedia.SEED.read_text(encoding="utf-8"))
        self.assertEqual(seed["license"], "CC BY-SA 4.0")
        self.assertGreater(len(seed["terms"]), 300)
        for t in seed["terms"]:
            self.assertTrue(0 < len(t["brief"]) <= 80, t)
        bad = [t["name"] for t in seed["terms"] if any(h in t["name"] + t["brief"] for h in wikipedia.POLITICAL_HINTS)]
        self.assertEqual(bad, [])

    def test_wikipedia_import_license_and_attribution(self):
        lib = self.new_home(seeds=("chime", "wikipedia"))
        wiki = [e for e in lib.book().entries if e["source"].get("dataset") == "Wikipedia"]
        self.assertGreater(len(wiki), 300)
        for e in wiki:
            self.assertEqual(e["source"]["license"], "CC BY-SA 4.0")
            self.assertIn("zh.wikipedia.org", e["source"]["url"])
            self.assertTrue(e["source"].get("revision"))

    def test_fetch_failure_is_reported_not_forced(self):
        saved = wikipedia._get
        wikipedia._get = lambda params: (_ for _ in ()).throw(OSError("no network"))
        try:
            out = Path(tempfile.mkdtemp(prefix="dlolib-fetch-")) / "raw.json"
            self.homes.append(out.parent)
            r = wikipedia.fetch(out, delay=0)
            self.assertFalse(r["ok"])
            self.assertFalse(out.exists())
        finally:
            wikipedia._get = saved

    def test_user_agent_is_explicit(self):
        self.assertIn("DigitalLifeLibrary", wikipedia.USER_AGENT)
        self.assertIn("https://", wikipedia.USER_AGENT)

    def test_yearly_lists(self):
        data = yearly.load()
        self.assertIn("2024", json.dumps(data["lists"]))
        bili = {l["year"]: l["terms"][0] for l in data["lists"] if l["list"] == "B站年度弹幕"}
        self.assertEqual(bili[2017], "囍")
        self.assertEqual(bili[2024], "接")
        self.assertEqual(sorted(bili), list(range(2017, 2026)))
        lib = self.new_home(seeds=("chime", "yearly"))
        e = lib.book().find_by_name("接")
        self.assertEqual(e["source"]["dataset"], "年度榜单")
        self.assertEqual(e["source"]["license"], "CC0-1.0")
        self.assertTrue(e["refs"] and e["refs"][0]["year"] == 2024)
        city = lib.book().find_by_name("city不city")
        self.assertTrue(any(r.get("year") == 2024 for r in city["refs"]))
        self.assertGreaterEqual(city["heat"], 2)
        n = len(lib.book().entries)
        yearly.run(lib.book())
        self.assertEqual(len(lib.book().entries), n)
        all_text = json.dumps(data, ensure_ascii=False)
        for word in ("命运共同体", "不忘初心", "新质生产力", "获得感"):
            self.assertNotIn(word, all_text)                 # 政治 / 政策类不收

    def test_only_one_name_per_term_in_yearly_briefs(self):
        data = yearly.load()
        for name, brief in data["briefs"].items():
            self.assertLessEqual(len(brief), 80, name)


# ───────────────────────── 书架 ─────────────────────────

def make_epub(path: Path, chapters: list):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        items = "".join(f'<item id="c{i}" href="c{i}.xhtml" media-type="application/xhtml+xml"/>' for i in range(len(chapters)))
        refs = "".join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))
        z.writestr("OEBPS/content.opf", f'<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
                                        f'<manifest>{items}</manifest><spine>{refs}</spine></package>')
        for i, text in enumerate(chapters):
            z.writestr(f"OEBPS/c{i}.xhtml", f"<html><body><h1>第{i + 1}章</h1><p>{text}</p><script>bad()</script></body></html>")


class ShelfTests(Base):
    def setUp(self):
        self.lib = self.new_home(seeds=())
        self.shelf = self.lib.shelf()
        self.tmp = Path(tempfile.mkdtemp(prefix="dlolib-books-"))
        self.homes.append(self.tmp)
        self.body = "".join(f"这是第{i}段。" + "月亮落在窗台上。" * 12 + "\n\n" for i in range(1, 41))
        self.txt = self.tmp / "小书.txt"
        self.txt.write_text(self.body, encoding="utf-8")

    def test_rights_required(self):
        r = self.shelf.add(self.txt, title="小书", rights="pirated", by="owner")
        self.assertFalse(r["ok"])
        self.assertEqual(self.shelf.list(), [])

    def test_add_txt_gb18030_html_epub(self):
        gb = self.tmp / "旧书.txt"
        gb.write_bytes("中文旧书的内容。\n第二行".encode("gb18030"))
        html = self.tmp / "页.html"
        html.write_text("<html><body><p>网页正文</p><script>evil()</script><p>第二段</p></body></html>", encoding="utf-8")
        epub = self.tmp / "书.epub"
        make_epub(epub, ["甲章的文字", "乙章的文字"])
        for f, t in [(self.txt, "小书"), (gb, "旧书"), (html, "页"), (epub, "电子书")]:
            r = self.shelf.add(f, title=t, rights="user-owned", by="owner")
            self.assertTrue(r["ok"], r)
        texts = {b["title"]: (self.shelf.dir / b["file"]).read_text(encoding="utf-8") for b in self.shelf._load()["books"]}
        self.assertIn("中文旧书的内容", texts["旧书"])
        self.assertIn("网页正文", texts["页"])
        self.assertNotIn("evil", texts["页"])
        self.assertIn("甲章的文字", texts["电子书"])
        self.assertIn("乙章的文字", texts["电子书"])
        self.assertNotIn("bad()", texts["电子书"])
        self.assertLess(texts["电子书"].index("甲章"), texts["电子书"].index("乙章"))
        again = self.shelf.add(self.txt, title="小书", rights="user-owned", by="owner")
        self.assertFalse(again["ok"])

    def test_bad_files_do_not_crash(self):
        bad = self.tmp / "坏.epub"
        bad.write_bytes(b"not a zip")
        self.assertFalse(self.shelf.add(bad, title="坏", rights="user-owned", by="owner")["ok"])
        odd = self.tmp / "x.pdf"
        odd.write_bytes(b"%PDF")
        self.assertFalse(self.shelf.add(odd, title="pdf", rights="user-owned", by="owner")["ok"])
        self.assertFalse(self.shelf.add(self.tmp / "不存在.txt", title="无", rights="user-owned", by="owner")["ok"])

    def test_read_progress_and_finish(self):
        self.shelf.add(self.txt, title="小书", rights="public-domain", by="owner")
        total = len(self.body)
        pos, got = 0, ""
        for _ in range(200):
            r = self.shelf.read("小书", "fish", chars=1500)
            self.assertTrue(r["ok"])
            self.assertEqual(r["from"], pos)
            self.assertTrue(r["text"].endswith("\n") or r["finished"])        # 停在段落结尾
            got += r["text"]
            pos = r["to"]
            if r["finished"]:
                break
        self.assertEqual(got.strip(), (self.shelf.dir / self.shelf._load()["books"][0]["file"]).read_text(encoding="utf-8").strip())
        self.assertEqual(self.shelf.list()[0]["status"]["fish"], "done")
        self.assertNotIn("sister", self.shelf.list()[0]["progress"])            # 各读各的进度
        r = self.shelf.read("小书", "fish", restart=True)
        self.assertEqual(r["from"], 0)
        self.assertLess(r["to"], total)

    def test_shared_reader_progress(self):
        self.shelf.add(self.txt, title="小书", rights="public-domain", by="owner")
        a = self.shelf.read("小书", "together", chars=1000)
        b = self.shelf.read("小书", "together", chars=1000)
        self.assertEqual(b["from"], a["to"])

    def test_daily_limit(self):
        self.lib.config["shelf"] = {"dailyChars": 2500}
        self.shelf.add(self.txt, title="小书", rights="public-domain", by="owner")
        total = 0
        for _ in range(20):
            r = self.shelf.read("小书", "fish", chars=1500)
            if not r["ok"]:
                self.assertIn("明天", r["error"])
                break
            total += len(r["text"])
        else:
            self.fail("每日上限没有生效")
        self.assertLessEqual(total, 2500 + 300)
        self.assertEqual(self.shelf.reading_log(1)[next(iter(self.shelf.reading_log(1)))]["fish"], total)
        self.assertTrue(self.shelf.read("小书", "sister", chars=1000)["ok"])       # 上限是各人各算

    def test_daily_limit_per_person_and_across_books(self):
        """dailyChars 可以按人写；今天读的字是所有书加起来算；一起读的字算在读的人头上。"""
        self.lib.config["shelf"] = {"dailyChars": {"default": 100000, "fish": 1200}}
        self.shelf.add(self.txt, title="小书", rights="public-domain", by="owner")
        other = self.tmp / "另一本.txt"
        other.write_text(self.body, encoding="utf-8")
        self.shelf.add(other, title="另一本", rights="public-domain", by="owner")
        self.assertEqual(self.shelf.daily_limit("fish"), 1200)
        self.assertEqual(self.shelf.daily_limit("sister"), 100000)
        r1 = self.shelf.read("小书", "together", chars=800, charge="fish")        # 一起读，算在 fish 头上
        self.assertTrue(r1["ok"])
        r2 = self.shelf.read("另一本", "fish", chars=800)                          # 换一本书也接着算
        self.assertTrue(r2["ok"])
        self.assertLessEqual(r2["todayChars"], 1200 + 300)
        r3 = self.shelf.read("另一本", "fish", chars=800)
        self.assertFalse(r3["ok"])
        self.assertTrue(r3["limitReached"])
        self.assertNotRegex(r3["error"], r"\d")                                    # 提示里不露出具体数字
        self.assertTrue(self.shelf.read("小书", "together", chars=800, charge="sister")["ok"])   # 姐姐的额度没动

    def test_together_reading_updates_both_statuses(self):
        self.shelf.add(self.txt, title="小书", rights="public-domain", by="owner")
        self.shelf.read("小书", "together", chars=600, charge="sister")
        st = self.shelf.list()[0]["status"]
        self.assertEqual((st["together"], st["sister"]), ("reading", "reading"))

    def test_status_and_bookmarks(self):
        self.shelf.add(self.txt, title="小书", rights="public-domain", by="owner")
        self.assertTrue(self.shelf.set_status("小书", "fish", "want")["ok"])
        self.assertFalse(self.shelf.set_status("小书", "fish", "bogus")["ok"])
        self.shelf.read("小书", "fish", chars=800)
        m = self.shelf.bookmark("小书", "fish", note="喜欢这里")["bookmark"]
        self.assertGreater(m["pos"], 0)
        self.assertTrue(m["quote"])
        self.shelf.bookmark("小书", "sister", note="另一个人的", pos=0)
        self.assertEqual(len(self.shelf.bookmarks("小书")), 2)
        self.assertEqual([x["note"] for x in self.shelf.bookmarks("小书", "fish")], ["喜欢这里"])
        with self.assertRaises(KeyError):
            self.shelf.read("没有这本", "fish")

    def test_epub_zip_bomb_guard(self):
        from dlolib import shelf as shelf_mod
        old = shelf_mod.MAX_BOOK_BYTES
        shelf_mod.MAX_BOOK_BYTES = 1000
        try:
            epub = self.tmp / "大.epub"
            make_epub(epub, ["字" * 5000])
            r = self.shelf.add(epub, title="大", rights="user-owned", by="owner")
            self.assertFalse(r["ok"])
        finally:
            shelf_mod.MAX_BOOK_BYTES = old


# ───────────────────────── HTTP 接口 ─────────────────────────

class ServerTests(Base):
    @classmethod
    def setUpClass(cls):
        cls.lib = cls.new_home(seeds=("chime",))
        cls.srv, cls.token = server.make_server(cls.lib, "127.0.0.1", 0)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        super().tearDownClass()

    def req(self, method, path, body=None, token="default", headers=None):
        token = self.token if token == "default" else token
        h = {"x-library-token": token} if token else {}
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        if data:
            h["Content-Type"] = "application/json"
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8")) if "json" in resp.headers["Content-Type"] else resp.read()
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_admin_page_and_health_open(self):
        code, page = self.req("GET", "/", token=None)
        self.assertEqual(code, 200)
        self.assertIn("图书馆管理页", page.decode("utf-8"))
        self.assertEqual(self.req("GET", "/api/library/health", token=None)[0], 200)

    def test_token_required(self):
        self.assertEqual(self.req("GET", "/api/library/stats", token=None)[0], 401)
        self.assertEqual(self.req("GET", "/api/library/stats", token="wrong")[0], 401)
        self.assertEqual(self.req("GET", "/api/library/stats", token=None, headers={"Authorization": f"Bearer {self.token}"})[0], 200)
        self.assertEqual(self.req("POST", "/api/library/action", {"action": "mark", "confirm": True}, token=None)[0], 401)

    def test_token_file_persisted(self):
        self.assertEqual((self.lib.home / "server.token").read_text(encoding="utf-8").strip(), self.token)
        self.assertEqual(server.load_or_create_token(self.lib), self.token)

    def test_reads(self):
        code, j = self.req("GET", "/api/library/books")
        self.assertEqual(code, 200)
        self.assertEqual(j["books"][0]["id"], "memes")
        self.assertIn("老大手填", j["labels"]["owner"])
        code, j = self.req("GET", "/api/library/list?source=seed&limit=5")
        self.assertEqual((code, len(j["items"])), (200, 5))
        self.assertGreater(j["total"], 1000)
        code, j = self.req("GET", "/api/library/search?q=%E5%B0%8A%E5%98%9F%E5%81%87%E5%98%9F")
        self.assertEqual(j["results"][0]["entry"]["name"], "尊嘟假嘟")
        eid = j["results"][0]["entry"]["id"]
        self.assertEqual(self.req("GET", f"/api/library/entry?id={eid}")[1]["entry"]["name"], "尊嘟假嘟")
        self.assertEqual(self.req("GET", "/api/library/entry?id=nope")[0], 404)
        self.assertEqual(self.req("GET", "/api/library/stats?book=nope")[0], 404)
        self.assertEqual(self.req("GET", "/api/library/list?limit=abc")[0], 400)
        self.assertEqual(self.req("GET", "/api/shelf/list")[0], 200)
        self.assertEqual(self.req("GET", "/api/nothing")[0], 404)

    def test_confirm_required_and_owner_forced(self):
        body = {"action": "add", "name": "接口加的梗", "brief": "意思"}
        code, j = self.req("POST", "/api/library/action", body)
        self.assertEqual(code, 400)
        self.assertIn("confirm", j["error"])
        self.assertIsNone(self.lib.book().find_by_name("接口加的梗"))
        code, j = self.req("POST", "/api/library/action", {**body, "confirm": "true"})      # 必须是布尔 true
        self.assertEqual(code, 400)
        code, j = self.req("POST", "/api/library/action", {**body, "confirm": True, "source": {"kind": "self", "by": "fish", "where": "x"}})
        self.assertEqual(code, 200)
        self.assertEqual(j["entry"]["source"], {"kind": "owner", "by": "owner"})           # 来源只能是手填

    def test_update_mark_remove_actions(self):
        eid = self.req("POST", "/api/library/action", {"action": "add", "name": "改我", "brief": "旧", "confirm": True})[1]["entry"]["id"]
        code, j = self.req("POST", "/api/library/action", {"action": "update", "id": eid, "fields": {"brief": "新"}, "confirm": True})
        self.assertEqual((code, j["entry"]["brief"]), (200, "新"))
        code, j = self.req("POST", "/api/library/action", {"action": "mark", "id": eid, "status": "dated", "hidden": True, "confirm": True})
        self.assertEqual((j["entry"]["status"], j["entry"]["hidden"]), ("dated", True))
        self.assertEqual(self.req("POST", "/api/library/action", {"action": "update", "id": eid, "fields": {"source": {}}, "confirm": True})[0], 400)
        self.assertEqual(self.req("POST", "/api/library/action", {"action": "explode", "confirm": True})[0], 400)
        self.assertEqual(self.req("POST", "/api/library/action", {"action": "add", "name": "号码", "brief": "13812345678", "confirm": True})[0], 400)
        self.assertEqual(self.req("POST", "/api/library/action", {"action": "remove", "id": eid, "confirm": True})[0], 200)

    def test_host_and_origin_checks(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.putrequest("GET", "/api/library/stats", skip_host=True)
        conn.putheader("Host", "evil.example.com")
        conn.putheader("x-library-token", self.token)
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 403)
        conn.close()
        code, _ = self.req("POST", "/api/library/action", {"action": "mark", "id": "x", "confirm": True},
                           headers={"Origin": "https://evil.example.com"})
        self.assertEqual(code, 403)

    def test_bad_bodies(self):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/library/action", data=b"not json", method="POST",
                                   headers={"x-library-token": self.token})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(r, timeout=10)
        self.assertEqual(cm.exception.code, 400)
        big = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/library/action", data=b"x" * (70 * 1024), method="POST",
                                     headers={"x-library-token": self.token})
        with self.assertRaises((urllib.error.HTTPError, ConnectionError, OSError)):
            urllib.request.urlopen(big, timeout=10)

    def test_refuses_non_local_bind(self):
        self.assertEqual(server.serve(self.lib, "0.0.0.0", 0), 2)

    def test_admin_page_has_no_dangerous_dom_writes(self):
        page = (server.WEB_DIR / "admin.html").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", page)
        self.assertNotIn("document.write", page)
        self.assertNotIn("http://", page.replace("http://www.w3.org", ""))        # 不引外部资源


# ───────────────────────── MCP ─────────────────────────

class McpTests(Base):
    def setUp(self):
        self.lib = self.new_home(seeds=("chime",))

    def rpc(self, h, method, params=None, mid=1):
        return h.handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})

    def text(self, h, tool, **args):
        r = self.rpc(h, "tools/call", {"name": tool, "arguments": args})
        return r["result"]["content"][0]["text"], r["result"].get("isError", False)

    def test_handshake_and_tools(self):
        h = mcp_server.Handler(self.lib, "fish")
        r = self.rpc(h, "initialize", {"protocolVersion": "2025-03-26"})
        self.assertEqual(r["result"]["protocolVersion"], "2025-03-26")
        self.assertIn("tools", r["result"]["capabilities"])
        self.assertIsNone(h.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        names = {t["name"] for t in self.rpc(h, "tools/list")["result"]["tools"]}
        self.assertEqual(names, {"library_lookup", "library_save", "library_mark", "library_scan",
                                 "shelf_list", "shelf_read", "shelf_status", "shelf_bookmark"})
        self.assertIn("error", self.rpc(h, "no/such"))

    def test_tool_descriptions_are_gentle(self):
        for t in mcp_server.TOOLS:
            blob = t["description"]
            for bad in ("钱", "费用", "token", "必须", "禁止"):
                self.assertNotIn(bad, blob, t["name"])

    def test_lookup_and_save_with_fixed_agent(self):
        fish = mcp_server.Handler(self.lib, "fish")
        sister = mcp_server.Handler(self.lib, "sister")
        text, err = self.text(fish, "library_lookup", query="蓝色大肥鱼")
        self.assertIn("书里没找到", text)
        text, err = self.text(fish, "library_save", name="蓝色大肥鱼", brief="DeepSeek 鲸鱼的外号", where="某个群")
        self.assertIn("存好了", text)
        e = self.lib.book().find_by_name("蓝色大肥鱼")
        self.assertEqual((e["source"]["kind"], e["source"]["by"], e["source"]["where"]), ("self", "fish", "某个群"))
        text, _ = self.text(sister, "library_save", name="某搜到的梗", brief="意思", url="https://example.org/a", by="fish")
        e2 = self.lib.book().find_by_name("某搜到的梗")
        self.assertEqual((e2["source"]["kind"], e2["source"]["by"]), ("asked", "sister"))        # by 参数被忽略，不能冒名
        text, _ = self.text(sister, "library_lookup", query="蓝色大肥鱼")
        self.assertIn("小鲸鱼存的", text)
        text, _ = self.text(fish, "library_save", name="蓝色大肥鱼", brief="重复", where="群")
        self.assertIn("没存成", text)
        text, _ = self.text(fish, "library_save", name="带号码", brief="加我13812345678", where="群")
        self.assertIn("没存成", text)

    def test_mark_scan_and_errors(self):
        h = mcp_server.Handler(self.lib, "fish")
        e = self.lib.book().find_by_name("泰裤辣")
        text, _ = self.text(h, "library_mark", id=e["id"], status="dated")
        self.assertIn("已更新", text)
        self.assertEqual(self.lib.book().get(e["id"])["history"][-1]["by"], "fish")
        self.assertIn("没有", self.text(h, "library_scan", message="今天天气不错")[0])
        text, err = self.text(h, "no_such_tool")
        self.assertTrue(err)

    def test_unknown_agent_refused(self):
        with self.assertRaises(ValueError):
            mcp_server.Handler(self.lib, "stranger")

    def test_shelf_tools(self):
        d = Path(tempfile.mkdtemp(prefix="dlolib-mcp-"))
        self.homes.append(d)
        f = d / "书.txt"
        f.write_text("第一段。\n\n" * 400, encoding="utf-8")
        self.lib.shelf().add(f, title="小书", rights="user-owned", by="owner")
        h = mcp_server.Handler(self.lib, "fish")
        self.assertIn("小书", self.text(h, "shelf_list")[0])
        t, err = self.text(h, "shelf_read", book="小书", chars=500)
        self.assertIn("第一段", t)
        self.assertIn("夹好了", self.text(h, "shelf_bookmark", book="小书", note="好")[0])

    def test_shelf_structured_results_and_status(self):
        """书架工具除了文字，还带结构化结果（接入程序用：读了多少字、读完没有、书签的位置）；文字照旧给 AI 看。"""
        d = Path(tempfile.mkdtemp(prefix="dlolib-mcp3-"))
        self.homes.append(d)
        f = d / "短书.txt"
        f.write_text("只有一小段文字。\n" * 30, encoding="utf-8")
        self.lib.shelf().add(f, title="短书", rights="user-owned", by="owner")
        h = mcp_server.Handler(self.lib, "fish")
        r = self.rpc(h, "tools/call", {"name": "shelf_read", "arguments": {"book": "短书", "chars": 6000}})["result"]
        sc = r["structuredContent"]
        self.assertTrue(sc["ok"] and sc["finished"] and sc["chars"] > 0 and sc["todayChars"] == sc["chars"])
        self.assertIn("这本读完了", r["content"][0]["text"])
        self.lib.config["shelf"] = {"dailyChars": {"default": 100000, "fish": sc["chars"]}}       # 把额度设成刚好读完
        r = self.rpc(h, "tools/call", {"name": "shelf_read", "arguments": {"book": "短书", "restart": True}})["result"]
        self.assertTrue(r["structuredContent"]["limitReached"])                    # 额度用完
        self.assertIn("明天", r["content"][0]["text"])
        r = self.rpc(h, "tools/call", {"name": "shelf_bookmark", "arguments": {"book": "短书", "note": "好"}})["result"]
        self.assertEqual(r["structuredContent"]["note"], "好")
        text, err = self.text(h, "shelf_status", book="短书", status="want")
        self.assertIn("想读", text)
        self.assertIn("（想读）", self.text(h, "shelf_list")[0])
        self.assertIn("没改成", self.text(h, "shelf_status", book="短书", status="bogus")[0])

    def test_read_together_shares_progress(self):
        d = Path(tempfile.mkdtemp(prefix="dlolib-mcp2-"))
        self.homes.append(d)
        f = d / "书.txt"
        f.write_text("".join(f"第{i}段的文字。" * 20 + "\n\n" for i in range(60)), encoding="utf-8")
        self.lib.shelf().add(f, title="合读书", rights="user-owned", by="owner")
        fish, sister = mcp_server.Handler(self.lib, "fish"), mcp_server.Handler(self.lib, "sister")
        a, _ = self.text(fish, "shelf_read", book="合读书", chars=600, together=True)
        b, _ = self.text(sister, "shelf_read", book="合读书", chars=600, together=True)
        self.assertNotEqual(a, b)                                    # 姐姐接着往下读，不是从头
        shelf = self.lib.shelf()
        prog = shelf.list()[0]["progress"]
        self.assertGreater(prog["together"], 0)
        self.assertNotIn("fish", prog)                               # 一起读不记在各人自己的进度上
        t, _ = self.text(fish, "shelf_read", book="合读书", chars=600)           # 自己读还是从头
        self.assertEqual(shelf.list()[0]["progress"]["fish"] < prog["together"], True)
        self.assertIn("一起读到", self.text(sister, "shelf_list")[0])
        self.assertIn("夹好了", self.text(fish, "shelf_bookmark", book="合读书", together=True)[0])
        self.assertEqual(len(shelf.bookmarks("合读书", "together")), 1)

    def test_stdio_process(self):
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "library_lookup", "arguments": {"query": "yyds"}}},
        ]
        p = subprocess.run([PYTHON, "-X", "utf8", "-m", "dlolib", "--home", str(self.lib.home), "mcp", "--agent", "fish"],
                           input=("\n".join(json.dumps(m, ensure_ascii=False) for m in msgs) + "\nnot json\n").encode("utf-8"),
                           capture_output=True, cwd=str(ROOT), env=env, timeout=60)
        lines = [json.loads(x) for x in p.stdout.decode("utf-8").splitlines() if x.strip()]
        self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
        self.assertEqual(lines[0]["id"], 1)
        self.assertIn("yyds", lines[1]["result"]["content"][0]["text"])
        self.assertEqual(lines[2]["error"]["code"], -32700)
        bad = subprocess.run([PYTHON, "-X", "utf8", "-m", "dlolib", "--home", str(self.lib.home), "mcp", "--agent", "nobody"],
                             input=b"", capture_output=True, cwd=str(ROOT), timeout=60)
        self.assertEqual(bad.returncode, 2)


# ───────────────────────── 导出 / 隐私扫描 ─────────────────────────

class ExportTests(Base):
    def setUp(self):
        self.lib = self.new_home(seeds=("chime", "wikipedia", "yearly"))
        self.book = self.lib.book()
        self.book.add(name="她们存的", brief="某群里的说法", source={"kind": "self", "by": "fish", "where": "某某测试群"},
                      origin="来自某某测试群里的一次聊天")
        self.out = Path(tempfile.mkdtemp(prefix="dlolib-out-")) / "pub"
        self.homes.append(self.out.parent)

    def test_export_default_only_licensed_seeds(self):
        r = export.export(self.lib, self.out)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["skipped"]["userEntries"], 1)
        files = sorted(p.name for p in (self.out / "memes").iterdir())
        self.assertEqual(files, ["entries.cc-by-sa-4.0.jsonl", "entries.cc0.jsonl", "entries.mit.jsonl"])
        counts = {p.name: sum(1 for _ in open(p, encoding="utf-8")) for p in (self.out / "memes").iterdir()}
        self.assertEqual(counts["entries.mit.jsonl"], 1457)
        for p in (self.out / "memes").iterdir():                          # 每个文件里的许可都和文件名一致
            want = {"mit": "MIT", "cc-by-sa-4.0": "CC BY-SA 4.0", "cc0": "CC0-1.0"}[p.name[len("entries."):-len(".jsonl")]]
            for line in open(p, encoding="utf-8"):
                e = json.loads(line)
                self.assertEqual(e["source"]["license"], want)
                self.assertNotIn("where", e["source"])
                self.assertEqual(e["history"], [{"at": e["createdAt"], "by": "import", "op": "add"}])
        blob = "".join(p.read_text(encoding="utf-8") for p in self.out.rglob("*") if p.is_file())
        self.assertNotIn("某某测试群", blob)
        self.assertIn("1457", blob)

    def test_hidden_entries_not_exported(self):
        e = self.book.find_by_name("鼠鼠")
        self.book.mark(e["id"], "owner", hidden=True)
        r = export.export(self.lib, self.out)
        self.assertTrue(r["ok"])
        self.assertEqual(r["skipped"]["hidden"], 1)

    def test_user_entries_need_license_and_are_stripped(self):
        self.assertFalse(export.export(self.lib, self.out, include_user_entries=True)["ok"])
        r = export.export(self.lib, self.out, include_user_entries=True, user_license="CC0-1.0")
        self.assertTrue(r["ok"], r)
        rows = [json.loads(x) for x in open(self.out / "memes" / "entries.community-cc0-1-0.jsonl", encoding="utf-8")]
        self.assertEqual(rows[0]["name"], "她们存的")
        self.assertEqual(rows[0]["source"], {"kind": "community", "license": "CC0-1.0"})
        self.assertNotIn("history", rows[0])
        self.assertNotIn("fish", json.dumps(rows[0]))

    def test_private_word_blocks_export_and_writes_nothing(self):
        words = Path(tempfile.mkdtemp(prefix="dlolib-words-")) / "words.txt"
        self.homes.append(words.parent)
        words.write_text("# 假的私有词表\n某某测试群\n鼠鼠\n", encoding="utf-8")
        r = export.export(self.lib, self.out, private_words=export.load_words([words]), include_user_entries=True,
                          user_license="CC0-1.0")
        self.assertFalse(r["ok"])
        self.assertTrue(r["hits"])
        self.assertNotIn("某某测试群", json.dumps(r, ensure_ascii=False))      # 报告里不回显命中的原文
        self.assertNotIn("鼠鼠", json.dumps(r, ensure_ascii=False))
        self.assertFalse(self.out.exists())

    def test_common_word_in_public_source_does_not_block_but_edited_entry_does(self):
        """私有词表里碰巧是普通词的词（公开原始数据里本来就有）不挡打底条目；人改过的条目仍按完整词表查。"""
        words = ["鼠鼠"]                                   # CHIME 原文里本来就有这个词
        self.assertTrue(export.export(self.lib, self.out, private_words=words)["ok"])
        shutil.rmtree(self.out)
        e = self.book.find_by_name("泰裤辣")
        self.book.update(e["id"], {"brief": "我改的，里面有鼠鼠"}, "owner")
        r = export.export(self.lib, self.out, private_words=words)
        self.assertFalse(r["ok"])
        self.assertTrue(any(":" in h for h in r["hits"]))

    def test_generic_patterns(self):
        cases = ["连到 192.168.1.20 上", "路径 C:\\Users\\someone\\x", "QQ号 12345678", "mail me a@b.org",
                 "token " + "a1" * 24, "sk-" + "A" * 24, "号码 1234567890"]
        for c in cases:
            self.assertTrue(export.scan_text(c, []), c)
        for c in ("2024年的梗", "第 1458 条", "version 0.1.0", "sha " + "a" * 64):
            self.assertEqual(export.scan_text(c, []), [], c)

    def test_allow_words_and_scan_tree(self):
        d = Path(tempfile.mkdtemp(prefix="dlolib-tree-"))
        self.homes.append(d)
        (d / "a.md").write_text("普通的话\n里面有张三这个名字\n", encoding="utf-8")
        (d / ".git").mkdir()
        (d / ".git" / "x.md").write_text("张三", encoding="utf-8")
        hits = export.scan_tree(d, ["张三"])
        self.assertEqual(len(hits), 1)
        self.assertIn("a.md:2", hits[0])
        self.assertNotIn("张三", hits[0])
        self.assertEqual(export.scan_tree(d, ["张三"], allow=["张三"]), [])

    def test_load_words(self):
        d = Path(tempfile.mkdtemp(prefix="dlolib-words2-"))
        self.homes.append(d)
        (d / "a.txt").write_text("# 注释\n 甲乙 \n\n丙\n丁戊己\n", encoding="utf-8")
        (d / "b.json").write_text('["庚辛", "壬"]', encoding="utf-8")
        self.assertEqual(sorted(export.load_words([d / "a.txt", d / "b.json"])), sorted(["甲乙", "丁戊己", "庚辛"]))
        with self.assertRaises(FileNotFoundError):
            export.load_words([d / "nope.txt"])


# ───────────────────────── 命令行 ─────────────────────────

class CliTests(Base):
    def run_cli(self, home, *args):
        p = subprocess.run([PYTHON, "-X", "utf8", "-m", "dlolib", "--home", str(home), *args], capture_output=True,
                           cwd=str(ROOT), timeout=120)
        return p.returncode, p.stdout.decode("utf-8"), p.stderr.decode("utf-8", "replace")

    def test_cli_flow(self):
        home = Path(tempfile.mkdtemp(prefix="dlolib-cli-"))
        self.homes.append(home)
        code, out, err = self.run_cli(home, "init", "--seed", "chime")
        self.assertEqual(code, 0, err)
        code, out, _ = self.run_cli(home, "find", "泰酷辣")
        self.assertIn("泰裤辣", out)
        code, out, _ = self.run_cli(home, "find", "蓝色大肥鱼")
        self.assertIn("书里没找到", out)
        code, out, err = self.run_cli(home, "add", "--name", "蓝色大肥鱼", "--brief", "鲸鱼的外号", "--kind", "owner")
        self.assertEqual(code, 0, err)
        code, out, _ = self.run_cli(home, "find", "蓝色大肥鱼")
        self.assertIn("主人手填", out)
        code, out, _ = self.run_cli(home, "add", "--name", "坏", "--brief", "x", "--kind", "self")
        self.assertEqual(code, 1)
        code, out, _ = self.run_cli(home, "list", "--source", "owner")
        self.assertIn("共 1 条", out)
        code, out, _ = self.run_cli(home, "scan", "今晚吃泰裤辣")
        self.assertIn("泰裤辣", out)
        code, out, _ = self.run_cli(home, "stats")
        self.assertEqual(json.loads(out)["bySource"]["owner"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
