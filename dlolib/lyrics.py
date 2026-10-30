"""
歌词梗白名单。

仓库的规矩是不收歌词（版权）。只有一种例外：某一两句歌词本身成了网络梗（比如「听雨的声音 → 一滴滴清晰」，
因为新宿决战的剪辑火起来的），这种可以收，但必须先登记在白名单 seeds/lyric_memes_whitelist.json 里。

白名单每首歌一条：歌名、演唱者、年份、成梗的上句（prev）/ 下句（next）、为什么成梗（why）、
和哪类剪辑 / 事件绑在一起（boundTo）、走红时间（popularSince）、依据链接（sources）。

规则（写在代码里，白名单文件改不了）：
  · 一首歌最多两句：上句 prev、下句 next，不收第三句；
  · 每句不超过 20 个字；
  · 另外可以登记至多 3 个「记混的变体」（variants，比如把「一滴滴」记成「一点点」），
    每个也不超过 20 字，而且得和上句 / 下句只差一两个字——不能借变体夹带别的句子；
  · 条目是「歌词梗」（type 歌词梗，或带 lyric 字段，或名字正好是白名单里的句子）时，
    name / aliases / prev / next 只能是白名单里这首歌的句子（或它们的拼接、登记过的变体）；
  · 不管是不是歌词梗，条目的文字里不能抄进一大段歌词（looks_like_lyrics 的启发式检查，
    白名单里的句子不算）。

校验由 store.Book.add / update / import_entries 调用（所有写入都经过那里），
所以命令行、MCP、HTTP 接口、导入脚本谁都绕不过。白名单文件缺了或坏了，按「没有白名单」处理（宁可少收）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .importers import SEEDS_DIR

LYRIC_TYPE = "歌词梗"
MAX_LINE_CHARS = 20          # 每句最多多少字
MAX_VARIANTS = 3
MAX_VARIANT_EDITS = 2        # 变体和上 / 下句最多差几个字
WHITELIST_PATH = SEEDS_DIR / "lyric_memes_whitelist.json"

NOT_LISTED = ("「{q}」不在歌词白名单里。歌词不收；只有已经成了梗的一两句（登记在 seeds/lyric_memes_whitelist.json）"
              "例外，想加的话请主人或维护者先往白名单里登记。")
DUMP_NOTE = "歌词不收（已经成了梗的那一两句除外），这条先不存～想存的话，只写梗名和自己的话就好。"


# ───────────────────────── 白名单 ─────────────────────────

def _store():
    from . import store          # 延迟导入：store 也要 import 这个模块
    return store


class Song:
    def __init__(self, raw: dict):
        self.id = raw["id"]
        self.title = raw["title"]
        self.artist = raw["artist"]
        self.year = raw["year"]
        self.prev = raw["prev"].strip()
        self.next = (raw.get("next") or "").strip()
        self.variants = [v.strip() for v in raw.get("variants", [])]
        self.why = raw["why"]
        self.bound_to = raw["boundTo"]
        self.popular_since = raw["popularSince"]
        self.sources = list(raw["sources"])
        n = _store().norm
        self.forms = {n(self.prev): "prev"}
        if self.next:
            self.forms[n(self.next)] = "next"
            self.forms[n(self.prev + self.next)] = "joined"
        for v in self.variants:
            self.forms[n(v)] = "variant"

    @property
    def lines(self) -> list:
        return [x for x in (self.prev, self.next) if x]

    def match(self, text: str) -> str:
        """text 是这首歌的哪种允许写法：prev / next / joined / variant；不是就返回空串。"""
        return self.forms.get(_store().norm(text), "")

    def phrases(self) -> list:
        return [*self.lines, *self.variants]

    def info(self) -> dict:
        return {"id": self.id, "song": self.title, "artist": self.artist, "year": self.year}


class Whitelist:
    def __init__(self, songs: list, problems: list):
        self.songs = songs
        self.problems = problems          # 白名单文件自己的毛病（对不上规则的歌被丢掉了）
        self.by_id = {s.id: s for s in songs}

    def find(self, text: str):
        """text 正好是哪首歌的允许写法，返回 (Song, 种类)；没有返回 (None, "")。"""
        for s in self.songs:
            kind = s.match(text)
            if kind:
                return s, kind
        return None, ""

    def phrases(self) -> list:
        out = [p for s in self.songs for p in s.phrases()]
        return sorted(set(out), key=len, reverse=True)


def _clean_line(text, label: str, problems: list, sid: str) -> str:
    if not isinstance(text, str) or not text.strip():
        problems.append(f"{sid}：{label}不能空")
        return ""
    t = text.strip()
    if len(t) > MAX_LINE_CHARS:
        problems.append(f"{sid}：{label}超过 {MAX_LINE_CHARS} 个字")
        return ""
    if re.search(r"[\r\n/／|｜]", t):
        problems.append(f"{sid}：{label}里不能有换行或斜杠（一句就是一句）")
        return ""
    return t


def validate(data) -> tuple:
    """检查白名单数据，返回 (Song 列表, 问题列表)。有问题的歌整首丢掉。"""
    problems, songs, seen = [], [], set()
    rows = data.get("songs") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return [], ["白名单格式不对：要有 songs 列表"]
    norm, lev = _store().norm, _store().lev
    for i, raw in enumerate(rows):
        sid = str(raw.get("id", f"#{i}")) if isinstance(raw, dict) else f"#{i}"
        if not isinstance(raw, dict):
            problems.append(f"{sid}：不是对象")
            continue
        errs = []
        extra = set(raw) - {"id", "title", "artist", "year", "prev", "next", "variants", "why", "boundTo",
                            "popularSince", "sources", "note"}
        if extra:
            errs.append(f"有不认识的字段：{'、'.join(sorted(extra))}（一首歌只登记 prev / next 两句）")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,40}", sid):
            errs.append("id 要是小写字母 / 数字 / 连字符")
        if sid in seen:
            errs.append("id 重复")
        for k in ("title", "artist"):
            if not (isinstance(raw.get(k), str) and raw[k].strip()):
                errs.append(f"{k} 不能空")
        if not (isinstance(raw.get("year"), int) and 1900 <= raw["year"] <= 2100):
            errs.append("year 要是歌发行的年份")
        for k, label in (("why", "为什么成梗（why）"), ("boundTo", "绑定的剪辑 / 事件（boundTo）")):
            if not (isinstance(raw.get(k), str) and len(raw[k].strip()) >= 6):
                errs.append(f"{label}要写清楚")
        if not (isinstance(raw.get("popularSince"), str) and re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", raw["popularSince"])):
            errs.append("popularSince 要写走红的年月（2026-07 或 2026-07-25）")
        srcs = raw.get("sources")
        if not (isinstance(srcs, list) and srcs and all(isinstance(u, str) and u.startswith("https://") for u in srcs)):
            errs.append("sources 至少一个 https 链接（成梗的依据）")
        prev = _clean_line(raw.get("prev"), "上句", errs, sid) if "prev" in raw else ""
        if not prev:
            errs.append("要有上句 prev")
        nxt = ""
        if raw.get("next") not in (None, ""):
            nxt = _clean_line(raw.get("next"), "下句", errs, sid)
        variants = raw.get("variants", [])
        if not (isinstance(variants, list) and len(variants) <= MAX_VARIANTS):
            errs.append(f"variants 最多 {MAX_VARIANTS} 个")
            variants = []
        for v in variants:
            vv = _clean_line(v, "变体", errs, sid)
            if vv and not any(lev(norm(vv), norm(x)) <= MAX_VARIANT_EDITS for x in (prev, nxt) if x):
                errs.append(f"变体「{vv}」和上 / 下句差太多（最多差 {MAX_VARIANT_EDITS} 个字）")
        if errs:
            problems += [f"{sid}：{e}" if not e.startswith(sid) else e for e in errs]
            continue
        seen.add(sid)
        songs.append(Song(raw))
    return songs, problems


_CACHE = {"key": None, "wl": None}


def load(path=None) -> Whitelist:
    """读白名单（按文件修改时间缓存，文件改了长驻进程也能读到新的）。文件缺了 / 坏了 → 空白名单，problems 里说明。"""
    p = Path(path) if path else Path(WHITELIST_PATH)
    try:
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        return Whitelist([], [f"找不到歌词白名单：{p.name}"])
    if _CACHE["key"] == key:
        return _CACHE["wl"]
    try:
        songs, problems = validate(json.loads(p.read_text(encoding="utf-8")))
    except (ValueError, OSError) as e:
        songs, problems = [], [f"歌词白名单读不了：{type(e).__name__}"]
    wl = Whitelist(songs, problems)
    _CACHE["key"], _CACHE["wl"] = key, wl
    return wl


# ───────────────────────── 条目校验 ─────────────────────────

def _lyric_id(e: dict) -> str:
    v = e.get("lyric")
    return str(v.get("id", "")) if isinstance(v, dict) else (str(v) if v else "")


def is_declared(e: dict) -> bool:
    return (e.get("type") == LYRIC_TYPE or bool((e.get("flags") or {}).get("lyricMeme")) or bool(e.get("lyric")))


def apply(e: dict, wl: Whitelist | None = None) -> str:
    """检查一条（拼好的）条目；是歌词梗就补全 type / lyric / prev / next。返回错误信息，空串 = 通过。

    什么算歌词梗：type 是「歌词梗」、带 lyric 字段、flags.lyricMeme，或者名字正好是白名单里的句子。
    普通条目原样放行（它的文字里有没有抄大段歌词，由 scrub / looks_like_lyrics 另外管）。"""
    wl = wl or load()
    name = str(e.get("name") or "")
    song, kind = None, ""
    lid = _lyric_id(e)
    if lid:
        song = wl.by_id.get(lid)
        if not song:
            return f"歌词白名单里没有这首歌（{lid}）"
    else:
        song, kind = wl.find(name)
    if song is None:
        if is_declared(e):
            return NOT_LISTED.format(q=name)
        return ""
    for label, text in [("名字", name), *[("别名", a) for a in e.get("aliases") or []],
                        ("上句", e.get("prev")), ("下句", e.get("next"))]:
        if not text:
            continue
        if not song.match(str(text)):
            return f"{label}「{text}」不是《{song.title}》白名单里的句子，歌词只收登记过的那一两句"
    if e.get("prev") and _store().norm(e["prev"]) != _store().norm(song.prev):
        return f"上句只能是《{song.title}》登记的那一句"
    if e.get("next") and _store().norm(e["next"]) != _store().norm(song.next):
        return f"下句只能是《{song.title}》登记的那一句"
    if not e.get("prev") and not e.get("next"):
        e["prev"], e["next"] = song.prev, song.next
        if not song.next:
            e.pop("next")
    e["type"] = LYRIC_TYPE
    e["lyric"] = song.info()
    norm = _store().norm
    aliases = list(e.get("aliases") or [])
    for line in song.lines:                      # 下句（或上句）也要能查到，查到就是这一条
        if norm(line) != norm(name) and norm(line) not in {norm(x) for x in aliases}:
            aliases.append(line)
    e["aliases"] = aliases
    return ""


# ───────────────────────── 抄进大段歌词的检查 ─────────────────────────

REDACTED = "（歌词略）"
# 「歌词是“……”」「唱的是“……”」：歌词字样后面紧跟着的引号
_QUOTE_AFTER_MARKER = re.compile(
    r"((?:歌词|歌里|歌中|唱的是|唱道|这首歌)[^“「『\"\n]{0,10}[“「『\"])([^”」』\"\n]+)([”」』\"])")
_COLON_LONG = re.compile(r"(歌词\s*[:：]\s*)([^\n“「『\"。]+)")      # 带引号的情况归上面那条管
_SPLIT = re.compile(r"[\r\n]+|\s[/／]\s|[|｜]")
_PUNCT = re.compile(r"[\s，。,.、；;！!？?~～…—\-“”「」『』\"'‘’]")


def _bare_len(text: str, phrases: list) -> int:
    for ph in phrases:
        text = text.replace(ph, "")
    return len(_PUNCT.sub("", text))


def redact(text, wl: Whitelist | None = None) -> tuple:
    """把文字里抄进来的大段歌词拿掉，返回 (处理后的文字, 有没有动过)。白名单里的句子不算歌词。
    启发式，宁可放过也别误伤正常释义。认三种写法：
      1. 分成三行以上（换行、「 / 」、竖线），每行都不长、平均不太短，也没有句号 / 冒号（不是对话，不是并列的几个别名）
         ——整段拿掉（返回空串）；
      2. 「歌词是“……”」「唱的是“……”」：引号里超过 20 个字（一句的量）——引号里换成「（歌词略）」；
      3. 「歌词：」后面直接跟着超过 20 个字——后面换成「（歌词略）」。
    释义里顺口引用一句歌词（20 字以内）不算大段。"""
    if not isinstance(text, str) or not text.strip():
        return text, False
    phrases = (wl or load()).phrases()
    shielded = text
    for ph in phrases:
        shielded = shielded.replace(ph, "")
    segs = [x.strip() for x in _SPLIT.split(shielded) if x.strip()]
    if (len(segs) >= 3 and max(len(x) for x in segs) <= 30 and sum(len(x) for x in segs) / len(segs) >= 6
            and not any(c in x for x in segs for c in "。：:")):
        return "", True
    hit = []

    def quote(m):
        if _bare_len(m.group(2), phrases) > MAX_LINE_CHARS:
            hit.append(1)
            return m.group(1) + REDACTED + m.group(3)
        return m.group(0)

    def colon(m):
        if _bare_len(m.group(2), phrases) > MAX_LINE_CHARS:
            hit.append(1)
            return m.group(1) + REDACTED
        return m.group(0)

    out = _COLON_LONG.sub(colon, _QUOTE_AFTER_MARKER.sub(quote, text))
    return out, bool(hit)


def looks_like_lyrics(text, wl: Whitelist | None = None) -> bool:
    return redact(text, wl)[1]
