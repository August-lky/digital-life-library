"""
维基百科「中国大陆网络用语列表」（zh.wikipedia.org，CC BY-SA 4.0）。

这个数据集的做法（也是为什么这样做）：
  1. fetch()：通过官方 MediaWiki API 读一次页面（variant=zh-cn，让维基自己把繁体转成简体），带清楚的
     User-Agent，请求之间隔几秒，一共 2 次请求。读下来的原文只放在 seeds/wikipedia/.cache/（不进仓库）。
  2. 词条名是事实，可以收；**释义必须自己写，不照抄原文**——所以仓库里放的 wikipedia_netslang.json
     只有「词条名 + 所在分区 + 自己写的短释义」，原文不转存。
  3. build_seed()：把 .cache 里的词条名和 briefs.json（自己写的释义）合成 wikipedia_netslang.json，
     去掉政治类 / 军事类整段，以及名字撞上 CHIME 的（CHIME 已经有了）。没写释义的词条跳过，不凑数。
  4. run()：把 wikipedia_netslang.json 导进书里，打底来源 dataset=Wikipedia，license=CC BY-SA 4.0，
     署名（条目名、链接、版本号）写在每条的 source 里。导出时 CC BY-SA 的数据单独一个文件，不和 MIT 混。

署名：维基百科「中国大陆网络用语列表」的贡献者，见页面历史。我们改了什么：转成简体（由维基 API 完成）、
去掉政治类和军事类、只取词条名并重写释义、按分区归类。
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from . import SEEDS_DIR
from ..store import norm

DATASET = "Wikipedia"
PAGE = "中国大陆网络用语列表"
API = "https://zh.wikipedia.org/w/api.php"
PAGE_URL = "https://zh.wikipedia.org/wiki/" + urllib.parse.quote(PAGE)
USER_AGENT = ("DigitalLifeLibrary/0.1 (https://github.com/August-lky/digital-life-library; "
              "low-volume read-only import of one public list; 2 requests per run)")
WIKI_DIR = SEEDS_DIR / "wikipedia"
CACHE = WIKI_DIR / ".cache" / "wikipedia_raw.json"
BRIEFS = WIKI_DIR / "briefs.json"
SEED = WIKI_DIR / "wikipedia_netslang.json"
LICENSE = "CC BY-SA 4.0"

# 只收这几个大分区。不收：政治类（老大定的）、军事类；体育运动类 / 企业媒体类 / 地域类 / 计量单位类里
# 主要是人名、俱乐部名、公司名、地名简称，不是网络用语。
ALLOW_H2 = ("形式", "动物类", "社交网站特有事物类", "日常生活类", "理论类", "教育类")
# 保险起见，词条名或原文里出现这些词的也不收（政治人物、政党、政权、时政事件）
POLITICAL_HINTS = ("共产党", "国民党", "政府", "政权", "总书记", "国家主席", "总理", "领导人", "习近平", "反腐",
                   "六四", "法轮", "民主运动", "台独", "港独", "游行", "示威", "维稳", "两会", "党中央")


class _Collector(HTMLParser):
    """从维基渲染后的 HTML 里取「分区 + 列表项里加粗的词条名 + 说明文字」。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.section, self.h2, self.items = "", "", []
        self._tag_stack, self._in_heading, self._heading_text = [], None, ""
        self._li_depth, self._cur, self._bold, self._skip = 0, None, None, 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("h2", "h3", "h4"):
            self._in_heading, self._heading_text = tag, ""
        elif tag == "li":
            self._li_depth += 1
            if self._li_depth == 1:
                self._cur = {"section": self.section, "h2": self.h2, "name": "", "text": ""}
        elif tag == "sup" and self._cur is not None:
            self._skip += 1                                    # 脚注编号
        elif tag == "b" and self._cur is not None and not self._cur["name"] and not self._cur["text"].strip():
            self._bold = ""
        elif tag == "ul" and a.get("class", "").startswith("gallery"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("h2", "h3", "h4") and self._in_heading == tag:
            title = re.sub(r"\[.*?\]", "", self._heading_text).strip()
            if tag == "h2":
                self.h2 = title
            self.section = title
            self._in_heading = None
        elif tag == "li":
            if self._li_depth == 1 and self._cur is not None:
                if self._cur["name"]:
                    self._cur["text"] = re.sub(r"\s+", " ", self._cur["text"]).strip("：: 。")
                    self.items.append(self._cur)
                self._cur = None
            self._li_depth = max(0, self._li_depth - 1)
        elif tag == "sup" and self._skip:
            self._skip -= 1
        elif tag == "b" and self._bold is not None:
            if self._cur is not None and not self._cur["name"]:
                self._cur["name"] = self._bold.strip()
            self._bold = None

    def handle_data(self, data):
        if self._in_heading:
            self._heading_text += data
        elif self._cur is not None and not self._skip:
            if self._bold is not None:
                self._bold += data
            elif self._cur["name"]:
                self._cur["text"] += data


def parse_html(page_html: str) -> list:
    c = _Collector()
    c.feed(page_html)
    return c.items


def _get(params: dict) -> dict:
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch(out=None, delay: float = 3.0) -> dict:
    """联网读一次（共 2 个请求，间隔 delay 秒）。读不到就如实说，不重试、不绕过。"""
    out = Path(out) if out else CACHE
    base = {"action": "parse", "page": PAGE, "variant": "zh-cn", "format": "json", "formatversion": "2"}
    try:
        meta = _get({**base, "prop": "revid"})
        time.sleep(max(1.0, delay))
        body = _get({**base, "prop": "text", "disableeditsection": "1", "disablelimitreport": "1"})
    except Exception as e:                                  # noqa: BLE001  网络问题就停下，由人决定怎么办
        return {"ok": False, "error": f"没读到维基页面（{type(e).__name__}）：不硬爬，稍后再试或手动处理"}
    items = parse_html(body["parse"]["text"])
    out.parent.mkdir(parents=True, exist_ok=True)
    data = {"page": PAGE, "url": PAGE_URL, "revid": meta["parse"]["revid"], "license": LICENSE,
            "variant": "zh-cn", "retrieved": time.strftime("%Y-%m-%d"), "items": items}
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return {"ok": True, "revid": data["revid"], "items": len(items), "cache": str(out)}


def is_excluded(item: dict) -> bool:
    """不收：不在收录分区里的，或带政治线索的。"""
    if item.get("h2") not in ALLOW_H2:
        return True
    blob = item["name"] + item.get("text", "")
    return any(h in blob for h in POLITICAL_HINTS)


def candidates(raw: dict, chime_names: set | None = None) -> list:
    """可以写释义的词条：不在跳过的分区、不带政治线索、名字没被 CHIME 收过、去重。"""
    seen, out = set(), []
    for it in raw["items"]:
        n = norm(it["name"])
        if not n or n in seen or is_excluded(it) or (chime_names and n in chime_names):
            continue
        seen.add(n)
        out.append(it)
    return out


def split_names(raw_name: str) -> tuple:
    """「吸猫、吸狗、吸兔」→ 主名 + 别名。每一段都至少两个字才拆（「二/2」这种不拆）。"""
    parts = [p.strip() for p in re.split(r"[/、]", raw_name) if p.strip()]
    if len(parts) > 1 and all(len(p) >= 2 for p in parts):
        return parts[0], parts[1:]
    return raw_name.strip(), []


def build_seed(raw: dict, briefs: dict, chime_names: set | None = None) -> dict:
    """把词条名和自己写的释义合成随项目带的种子文件。没写释义的不收；名字 CHIME 已经有的也不重复收。"""
    terms = []
    for it in candidates(raw, chime_names):
        it = {**it, "name": it["name"].rstrip("：:")}
        b = briefs.get(it["name"])
        if not b:
            continue
        brief, flags = (b["brief"], b.get("flags", {})) if isinstance(b, dict) else (b, {})
        name, aliases = split_names(it["name"])
        t = {"name": name, "section": it["section"], "brief": brief}
        if aliases:
            t["aliases"] = aliases
        if flags:
            t["flags"] = flags
        terms.append(t)
    return {"dataset": DATASET, "page": raw["page"], "url": raw["url"], "revision": raw["revid"], "license": LICENSE,
            "retrieved": raw["retrieved"], "variant": raw["variant"],
            "attribution": f"词条名取自维基百科「{raw['page']}」（{raw['url']}，版本 {raw['revid']}，"
                           "作者见页面历史），CC BY-SA 4.0；释义为本项目自己所写。",
            "changes": "由维基 API 转成简体；去掉政治类、军事类及体育 / 企业等非网络用语分区；只取词条名并重写释义。",
            "terms": terms}


def build_entries(book, seed: dict) -> list:
    out = []
    for t in seed["terms"]:
        src = {"kind": "seed", "by": "import", "dataset": DATASET, "license": LICENSE, "url": seed["url"],
               "page": seed["page"], "revision": seed["revision"]}
        out.append(book.build_entry(name=t["name"], brief=t["brief"], source=src, type=t.get("section", ""),
                                    aliases=t.get("aliases"), flags=t.get("flags"),
                                    at=seed["retrieved"] + "T00:00:00Z"))
    return out


def build_files(chime_json=None) -> dict:
    """用 .cache 里读下来的词条名 + briefs.json 里自己写的释义，生成随项目带的 wikipedia_netslang.json。"""
    raw = json.loads(CACHE.read_text(encoding="utf-8"))
    briefs = {k: v for k, v in json.loads(BRIEFS.read_text(encoding="utf-8")).items() if not k.startswith("_")}
    chime = None
    chime_file = Path(chime_json) if chime_json else SEEDS_DIR / "chime" / "chime_full.json"
    if chime_file.exists():
        chime = {norm(x["meme"]) for x in json.loads(chime_file.read_text(encoding="utf-8"))}
    seed = build_seed(raw, briefs, chime)
    SEED.write_text(json.dumps(seed, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    return {"ok": True, "terms": len(seed["terms"]), "file": str(SEED)}


def run(book, path=None) -> dict:
    p = Path(path) if path else SEED
    if not p.exists():
        return {"ok": False, "error": "还没有 wikipedia_netslang.json（先 fetch-wikipedia 再写释义生成，或用随项目带的）"}
    seed = json.loads(p.read_text(encoding="utf-8"))
    res = book.import_entries(build_entries(book, seed), DATASET)
    res["source"] = len(seed["terms"])
    return res


__all__ = ["fetch", "parse_html", "candidates", "build_seed", "run"]
