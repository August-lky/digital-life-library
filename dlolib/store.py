"""
工具书（reference book）的存储、索引、检索、增删改。一本书 = <home>/books/<id>/entries.jsonl，一行一条。

这一层是将来要开源的核心：不认识任何具体的 AI、群、聊天软件。「谁存的」只是一个 agent id 字符串，
合法的 agent 列表、来源的显示名都从 <home>/library.config.json 读。

为什么这样存：
  - 一行一条的 JSONL，人能直接看、能 diff；几千条的量整本读进内存再查，几毫秒，比上数据库简单。
  - 写的时候先拿锁文件，再整本写到临时文件、改名替换，两个进程同时写也不会写坏。
  - 语义向量可选（fastembed + BAAI/bge-small-zh-v1.5）。没装、或本机没有模型缓存，就只用关键词，照样能查。

检索要对付「半句话」「变体写法」：
  1. 归一：全角转半角、小写、去标点空格、三个以上相同字收成两个（「绝绝绝子」→「绝绝子」）。
  2. 名字 / 别名：完全一样 > 一句话里含着梗名 > 梗名里含着这半句 > 字二元组重合 > 编辑距离（换了一个字的变体）。
  3. 出处和例句：≥4 字的半句话去出处原话、例句里找（「妹妹长城美不美」→ city不city）。
  4. 读音（可选）：装了 pypinyin 才有，认「尊嘟假嘟 ~ 真的假的」这类谐音写法，见 phonetic.py。
  5. 语义（可选）：按意思找，分数压低，只当「可能是」。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import unicodedata
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import phonetic

SOURCE_KINDS = ("seed", "asked", "self", "owner")
DEFAULT_LABELS = {"seed": "打底", "asked": "{owner}让搜的", "self": "自己学的", "owner": "{owner}手填"}
STATUSES = {"active": "正流行", "fading": "有点过气", "dated": "过时了", "unknown": "不清楚"}
EDITABLE = {"name", "aliases", "brief", "meaning", "origin", "examples", "era", "status", "heat",
            "hidden", "flags", "refs", "passive", "type"}
BRIEF_MAX = 80
NAME_MAX = 30
HISTORY_MAX = 20

DEFAULT_BOOKS = {
    "books": [{
        "id": "memes", "title": "中文互联网热梗", "dir": "books/memes", "kind": "reference",
        "about": "网络流行语、梗、谐音、缩写、名场面台词。歌词不收。",
        "passiveDefault": False,
    }]
}

# 不收录个人信息：手机号、身份证、邮箱、「QQ 123456」这类号码（防有人把别人的信息「存成梗」）
PRIVATE_RE = re.compile(
    r"(?<!\d)1[3-9]\d{9}(?!\d)"
    r"|(?<!\d)\d{17}[\dXx](?!\d)"
    r"|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"|(?:QQ|qq|Q号|群号|微信号?|wx)\s*[:：号]?\s*[A-Za-z0-9_-]{5,20}"
)


# ───────────────────────── 小工具 ─────────────────────────

_KEEP = re.compile(r"[0-9a-z㐀-鿿豈-﫿]")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s or "")).lower()
    s = "".join(ch for ch in s if _KEEP.match(ch))
    return re.sub(r"(.)\1{2,}", r"\1\1", s)


def bigrams(s: str) -> set:
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_id(book_id: str, name: str) -> str:
    return book_id[:1] + "-" + hashlib.sha1(norm(name).encode("utf-8")).hexdigest()[:10]


def has_private(obj) -> bool:
    return bool(PRIVATE_RE.search(json.dumps(obj, ensure_ascii=False)))


@contextmanager
def locked(path: Path, timeout: float = 5.0):
    lock = Path(str(path) + ".lock")
    t0 = time.time()
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 30:   # 30 秒以上的锁当成崩掉留下的
                    lock.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.time() - t0 > timeout:
                raise TimeoutError("书正被别人改着，过一会儿再试")
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def synchronized(method):
    """同一个进程里多个线程（HTTP 服务）同时查、同时重新读书时，别让它们看到读了一半的索引。"""
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    wrapper.__name__, wrapper.__doc__ = method.__name__, method.__doc__
    return wrapper


def write_atomic(path: Path, text: str):
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    # Windows 上，别的进程正好打开着目标文件读的话，替换会被拒绝（PermissionError）：稍等重试
    for attempt in range(40):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.05)


# ───────────────────────── 图书馆（一个 home 目录）─────────────────────────

class Library:
    """一个图书馆 = 一个 home 目录：library.config.json、books.json、books/、shelf/。"""

    def __init__(self, home):
        self.home = Path(home).resolve()
        self.config = self._read_json("library.config.json", {})
        self._books = {}

    @classmethod
    def init_home(cls, home, owner_name: str = "主人", agents: dict | None = None) -> "Library":
        """建一个新的图书馆目录：books.json（只有一本「梗」）、library.config.json、books/memes/。已有的不覆盖。"""
        home = Path(home)
        (home / "books" / "memes").mkdir(parents=True, exist_ok=True)
        if not (home / "books.json").exists():
            write_atomic(home / "books.json", json.dumps(DEFAULT_BOOKS, ensure_ascii=False, indent=2) + "\n")
        if not (home / "library.config.json").exists():
            cfg = {"ownerName": owner_name, "agents": agents or {}, "shelf": {"dailyChars": None}}
            write_atomic(home / "library.config.json", json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
        return cls(home)

    def _read_json(self, name, default):
        p = self.home / name
        if not p.exists():
            return default
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    # 配置
    @property
    def owner_name(self) -> str:
        return self.config.get("ownerName", "主人")

    @property
    def agents(self) -> dict:
        return self.config.get("agents", {})

    def label(self, kind: str) -> str:
        labels = {**DEFAULT_LABELS, **self.config.get("sourceLabels", {})}
        return labels.get(kind, kind).replace("{owner}", self.owner_name)

    def agent_name(self, agent_id: str) -> str:
        if agent_id == "owner":
            return self.owner_name
        a = self.agents.get(agent_id)
        return (a or {}).get("name", agent_id) if isinstance(a, dict) else (a or agent_id)

    # 书架
    def book_list(self) -> list:
        return self._read_json("books.json", {"books": []})["books"]

    def book(self, book_id: str = "") -> "Book":
        books = {b["id"]: b for b in self.book_list() if b.get("kind", "reference") == "reference"}
        if not book_id:
            book_id = next(iter(books), "")
        if book_id not in books:
            raise KeyError(f"没有这本工具书：{book_id or '（空）'}（有：{'、'.join(books) or '还没有'}）")
        b = self._books.get(book_id)
        if b is None:
            b = self._books[book_id] = Book(self, book_id, self.home / books[book_id]["dir"])
        b.maybe_reload()
        return b

    def shelf(self):
        from .shelf import Shelf
        return Shelf(self)


class Book:
    """一本工具书：读进内存、查、改。"""

    def __init__(self, lib: Library, book_id: str, directory: Path):
        self.lib = lib
        self.id = book_id
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "entries.jsonl"
        self.entries: list = []
        self._mtime = None
        self._keys: list = []
        self._texts: list = []
        self._syl: list = []
        self._vec = None
        self._lock = threading.RLock()
        self.reload()

    # ── 读 ──
    @synchronized
    def reload(self):
        self.entries = []
        if self.file.exists():
            with open(self.file, encoding="utf-8") as f:
                self.entries = [json.loads(line) for line in f if line.strip()]
            self._mtime = self.file.stat().st_mtime
        self._index()

    @synchronized
    def maybe_reload(self):
        if self.file.exists() and self.file.stat().st_mtime != self._mtime:
            self.reload()

    def _index(self):
        self._keys, self._texts, self._syl = [], [], []
        use_py = phonetic.available()
        for e in self.entries:
            keys, syls = [], []
            for k in [e["name"], *e.get("aliases", [])]:
                nk = norm(k)
                if nk:
                    keys.append((nk, bigrams(nk), nk != norm(e["name"])))
                    if use_py:
                        syl = phonetic.syllables(nk)
                        if syl and len(syl) >= 3:
                            syls.append(syl)
            self._keys.append(keys)
            self._syl.append(syls)
            self._texts.append(norm(" ".join([e.get("origin") or "", *e.get("examples", [])])))
        self._vec = None

    def get(self, entry_id: str):
        return next((e for e in self.entries if e["id"] == entry_id), None)

    def find_by_name(self, name: str):
        n = norm(name)
        for e, keys in zip(self.entries, self._keys):
            if any(k == n for k, _, _ in keys):
                return e
        return None

    # ── 查 ──
    def _lexical(self, q: str, qb: set, keys) -> tuple:
        best, why = 0.0, ""
        for key, kb, is_alias in keys:
            if q == key:
                s, w = 1.0, ("别名一样" if is_alias else "名字一样")
            elif len(key) == 2 and key in q and not re.search(r"[a-z0-9]", key):
                # 两个字的梗名（「可爱」「偶像」）塞在一整句话里，多半只是个普通词：分数压低，排在长梗名后面
                s, w = 0.5 + 0.1 * len(key) / len(q), "话里有这个词（两个字，不一定是梗）"
            elif len(key) >= 2 and key in q:
                s, w = 0.82 + 0.13 * len(key) / len(q), "话里有这个梗"
            elif len(q) >= 2 and q in key and (len(q) >= 3 or len(q) * 2 >= len(key)):
                # 两个字的半句（「好的」）只认短梗名，不然长梗名里随便一个常用词都会中
                s, w = 0.55 + 0.4 * len(q) / len(key), "是这个梗的半句"
            else:
                inter = len(qb & kb)
                dice = 2 * inter / (len(qb) + len(kb))
                cover = inter / len(kb)
                s, w = 0.75 * dice, "写法相近"
                if inter and len(key) >= 3 and cover >= 0.5 and (inter >= 2 or cover == 1):
                    s = max(s, 0.45 + 0.4 * cover * min(1.0, len(key) / max(len(q), 1) + 0.3))
                # 换了一个字的变体（「永远滴神」「泰酷辣」）：二元组几乎全断，但编辑距离只差一
                if s < 0.6 and len(key) >= 3 and abs(len(q) - len(key)) <= 2:
                    sim = 1 - lev(q, key) / max(len(q), len(key))
                    if sim >= 0.6:
                        s = max(s, 0.8 * sim)
            if s > best:
                best, why = s, w
        return best, why

    @synchronized
    def search(self, query: str, k: int = 5, semantic: bool = False, include_hidden: bool = False,
               min_score: float = 0.4) -> list:
        q = norm(query)
        if not q:
            return []
        qb = bigrams(q)
        qsyl = phonetic.syllables(q) if phonetic.available() else None
        rows = {}
        for i, (e, keys) in enumerate(zip(self.entries, self._keys)):
            if e.get("hidden") and not include_hidden:
                continue
            best, why = self._lexical(q, qb, keys)
            if qsyl and best < 0.8:
                for key_syl in self._syl[i]:
                    ps = phonetic.phonetic_score(qsyl, key_syl)
                    if ps and ps[0] > best:
                        best, why = ps
            if best < 0.6 and len(q) >= 4:
                t = self._texts[i]
                if q in t:
                    s = 0.62
                else:
                    cov = len(qb & bigrams(t)) / len(qb)
                    s = 0.5 * cov if cov >= 0.75 else 0
                if s > best:
                    best, why = s, "出处或例句里有这句"
            if best >= min_score:
                rows[i] = (best, why)

        if semantic:
            sims = self._semantic(query)
            if sims is not None:
                for i, sim in enumerate(sims):
                    e = self.entries[i]
                    if (e.get("hidden") and not include_hidden) or sim < 0.58:
                        continue
                    s = min(0.6, 0.3 + 2 * (float(sim) - 0.58))   # 余弦 0.58→0.3；只当「可能是」
                    if s > rows.get(i, (0, ""))[0]:
                        rows[i] = (s, "意思可能相近")

        ranked = sorted(rows.items(), key=lambda kv: -kv[1][0])[:k]
        return [{"score": round(s, 3), "why": w, "entry": self.entries[i]} for i, (s, w) in ranked]

    @synchronized
    def scan(self, message: str, limit: int = 2) -> list:
        """被动模式：一句话里出现了书里的梗名就挑出来。只认 3 个字以上、或带字母的梗名；
        两个字的常用词（「套路」「机智」）太容易误中，除非条目写了 passive:true。过时、隐藏、冒犯的不提示。"""
        m = norm(message)
        hits = []
        for e, keys in zip(self.entries, self._keys):
            if e.get("hidden") or e.get("status") == "dated" or e.get("flags", {}).get("offense"):
                continue
            allow = e.get("passive")
            for key, _, _ in keys:
                ok = allow if allow is not None else (len(key) >= 3 or bool(re.search(r"[a-z]", key)))
                if ok and len(key) >= 2 and key in m:
                    hits.append((len(key), e))
                    break
        hits.sort(key=lambda h: -h[0])
        return [e for _, e in hits[:limit]]

    def _semantic(self, query: str):
        try:
            import numpy as np
            model = embed_model()
            if model is None:
                return None
            if self._vec is None:
                self._vec = self._load_vectors(model, np)
            return self._vec @ np.array(list(model.embed([query])))[0]
        except Exception:
            return None

    def _load_vectors(self, model, np):
        idx = self.dir / ".index"
        idx.mkdir(exist_ok=True)
        texts = [f"{e['name']}：{e.get('brief') or e.get('meaning') or ''}" for e in self.entries]
        sig = hashlib.sha1("\n".join(texts).encode("utf-8")).hexdigest()
        vf, sf = idx / "vectors.npy", idx / "vectors.sig"
        if vf.exists() and sf.exists() and sf.read_text(encoding="utf-8") == sig:
            return np.load(vf)
        vec = np.array(list(model.embed(texts)), dtype="float32")
        np.save(vf, vec)
        sf.write_text(sig, encoding="utf-8")
        return vec

    # ── 写 ──
    @synchronized
    def _save(self):
        write_atomic(self.file, "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in self.entries))
        self._mtime = self.file.stat().st_mtime
        self._index()

    def _check_source(self, source: dict) -> str:
        kind = source.get("kind")
        if kind not in SOURCE_KINDS:
            return f"source.kind 只能是 {'/'.join(SOURCE_KINDS)}"
        by = source.get("by", "")
        agents = self.lib.agents
        if kind == "seed":
            if not source.get("dataset") or not source.get("license"):
                return "打底的条目要写清数据集名（source.dataset）和许可（source.license）"
        if kind in ("asked", "self"):
            if not by:
                return "要写清是谁存的（source.by）"
            if agents and by not in agents:
                return f"不认识的 agent：{by}（library.config.json 里登记的有：{'、'.join(agents)}）"
        if kind == "self" and not source.get("where"):
            return "写一下在哪碰到的（source.where，比如某个群、刷到的视频）"
        if kind == "asked" and not (source.get("url") or source.get("note")):
            return "写一下在哪查到的（网址，或一句说明）"
        if kind == "owner" and by not in ("", "owner"):
            return "手填的条目，by 只能是 owner"
        return ""

    def build_entry(self, *, name, brief, source: dict, aliases=None, origin="", examples=None, era="",
                    refs=None, flags=None, meaning="", type="", status=None, heat=None, brief_by="",
                    at=None) -> dict:
        """按统一格式拼出一条完整的条目（不写盘）。add 和导入脚本都用它，格式只有这一处。"""
        at = at or now_iso()
        kind = source["kind"]
        entry = {
            "id": make_id(self.id, name), "book": self.id, "name": name, "aliases": list(aliases or []),
            "brief": brief, "meaning": meaning or "", "origin": origin or "",
            "examples": [str(x) for x in (examples or [])][:3], "type": type or "", "era": era or "",
            "status": status or ("unknown" if kind == "seed" else "active"), "heat": heat,
            "hidden": False, "flags": dict(flags or {}), "refs": list(refs or []),
            "source": source, "createdAt": at, "updatedAt": at,
            "history": [{"at": at, "by": source.get("by", "import"), "op": "add"}],
        }
        if brief_by:
            entry["briefBy"] = brief_by
        return entry

    def add(self, *, name, brief, source: dict, aliases=None, origin="", examples=None, era="",
            refs=None, flags=None, meaning="", type="") -> dict:
        name, brief = str(name or "").strip(), str(brief or "").strip()
        if not name:
            return {"ok": False, "error": "梗的名字不能空"}
        if len(name) > NAME_MAX:
            return {"ok": False, "error": f"名字太长了，{NAME_MAX} 字以内"}
        if not brief:
            return {"ok": False, "error": "写一句它是什么意思（用自己的话）"}
        if len(brief) > BRIEF_MAX:
            return {"ok": False, "error": f"意思写短一点，{BRIEF_MAX} 字以内"}
        source = {k: v for k, v in (source or {}).items() if v not in (None, "")}
        if source.get("kind") == "owner":
            source["by"] = "owner"
        if source.get("kind") == "seed":
            source.setdefault("by", "import")
        err = self._check_source(source)
        if err:
            return {"ok": False, "error": err}
        if has_private([name, brief, origin, examples, aliases, meaning, source]):
            return {"ok": False, "error": "里面像是有号码、手机号或邮箱，这种个人信息不收"}
        aliases = [str(a).strip() for a in (aliases or []) if str(a).strip()]
        with locked(self.file):
            self.maybe_reload()
            for n in [name, *aliases]:
                dup = self.find_by_name(n)
                if dup:
                    return {"ok": False, "error": f"书里已经有「{dup['name']}」了，想补充可以改那一条", "existing": dup}
            entry = self.build_entry(name=name, brief=brief, source=source, aliases=aliases, origin=origin,
                                     examples=examples, era=era, refs=refs, flags=flags, meaning=meaning,
                                     type=type)
            self.entries.append(entry)
            self._save()
            return {"ok": True, "entry": entry}

    def import_entries(self, entries: list, dataset: str) -> dict:
        """批量导入一个数据集的打底条目（一次拿锁、一次写盘）。

        - 这个数据集以前导进来、而且没被人改过的条目：换成新的（重导入不会越堆越多）；
        - 被人改过的（history 里有非导入的人改过）：保留他们改的版本，不覆盖；
        - 名字或别名撞上别的来源的条目：跳过，并记下来。
        """
        with locked(self.file):
            self.maybe_reload()
            keep, replaced, edited = [], 0, set()
            for e in self.entries:
                s = e.get("source", {})
                if s.get("kind") == "seed" and s.get("dataset") == dataset:
                    if any(h.get("by") != "import" for h in e.get("history", [])):
                        keep.append(e)
                        edited.add(e["id"])
                    else:
                        replaced += 1
                else:
                    keep.append(e)
            taken = {}
            for e in keep:
                for n in [e["name"], *e.get("aliases", [])]:
                    taken[norm(n)] = e["name"]
            added, skipped = [], []
            for e in entries:
                if e["id"] in edited:
                    continue
                names = [e["name"], *e.get("aliases", [])]
                clash = next((taken[norm(n)] for n in names if norm(n) in taken), None)
                if clash:
                    skipped.append({"name": e["name"], "clashWith": clash})
                    continue
                for n in names:
                    taken[norm(n)] = e["name"]
                added.append(e)
            self.entries = keep + added
            self._save()
            return {"ok": True, "added": len(added), "replaced": replaced, "keptEdited": len(edited),
                    "skipped": skipped}

    def update(self, entry_id: str, fields: dict, by: str) -> dict:
        bad = set(fields) - EDITABLE
        if bad:
            return {"ok": False, "error": f"这些不能改：{'、'.join(sorted(bad))}"}
        if "status" in fields and fields["status"] not in STATUSES:
            return {"ok": False, "error": f"status 只能是 {'/'.join(STATUSES)}"}
        if "brief" in fields and not (0 < len(str(fields["brief"]).strip()) <= BRIEF_MAX):
            return {"ok": False, "error": f"意思不能空，{BRIEF_MAX} 字以内"}
        if "name" in fields and not (0 < len(str(fields["name"]).strip()) <= NAME_MAX):
            return {"ok": False, "error": f"名字不能空，{NAME_MAX} 字以内"}
        if has_private(fields):
            return {"ok": False, "error": "里面像是有号码、手机号或邮箱，这种个人信息不收"}
        with locked(self.file):
            self.maybe_reload()
            e = self.get(entry_id)
            if not e:
                return {"ok": False, "error": f"没有这一条：{entry_id}"}
            changed = {k: v for k, v in fields.items() if e.get(k) != v}
            if not changed:
                return {"ok": True, "entry": e, "note": "没有变化"}
            if "name" in changed or "aliases" in changed:
                for n in [changed.get("name", e["name"]), *changed.get("aliases", e.get("aliases", []))]:
                    dup = self.find_by_name(n)
                    if dup and dup["id"] != e["id"]:
                        return {"ok": False, "error": f"书里已经有「{dup['name']}」了"}
            at = now_iso()
            before = {k: e.get(k) for k in changed}
            e.update(changed)
            if "brief" in changed:
                e.pop("briefBy", None)          # 自动截的短释义被人改过了，就不再标 auto
            e["updatedAt"] = at
            e.setdefault("history", []).append({"at": at, "by": by, "op": "update", "fields": sorted(changed),
                                                "before": before})
            e["history"] = e["history"][-HISTORY_MAX:]
            self._save()
            return {"ok": True, "entry": e}

    def mark(self, entry_id: str, by: str, status=None, hidden=None) -> dict:
        f = {}
        if status is not None:
            f["status"] = status
        if hidden is not None:
            f["hidden"] = bool(hidden)
        if not f:
            return {"ok": False, "error": "要给 status 或 hidden"}
        return self.update(entry_id, f, by)

    def remove(self, entry_id: str, by: str) -> dict:
        """把一条从书里拿走。不是真删：整条连同历史存进 removed.jsonl，想找回可以翻那个文件。"""
        with locked(self.file):
            self.maybe_reload()
            e = self.get(entry_id)
            if not e:
                return {"ok": False, "error": f"没有这一条：{entry_id}"}
            gone = {**e, "removedAt": now_iso(), "removedBy": by}
            with open(self.dir / "removed.jsonl", "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(gone, ensure_ascii=False) + "\n")
            self.entries = [x for x in self.entries if x["id"] != entry_id]
            self._save()
            return {"ok": True, "removed": e["name"]}

    # ── 列表 / 统计（管理页用）──
    def list(self, source="", by="", status="", q="", include_hidden=True, offset=0, limit=50) -> dict:
        if q:
            pool = [r["entry"] for r in self.search(q, k=200, include_hidden=include_hidden, min_score=0.35)]
        else:
            pool = sorted(self.entries, key=lambda e: e.get("updatedAt") or "", reverse=True)
        rows = [e for e in pool
                if (not source or e["source"].get("kind") == source)
                and (not by or e["source"].get("by") == by)
                and (not status or (status == "hidden" and e.get("hidden")) or e.get("status") == status)
                and (include_hidden or not e.get("hidden"))]
        return {"total": len(rows), "items": rows[offset:offset + limit]}

    def stats(self) -> dict:
        return {
            "book": self.id, "total": len(self.entries),
            "bySource": dict(Counter(e["source"].get("kind") for e in self.entries)),
            "byAgent": dict(Counter(e["source"].get("by", "") for e in self.entries if e["source"].get("by"))),
            "byDataset": dict(Counter(e["source"].get("dataset") for e in self.entries
                                      if e["source"].get("dataset"))),
            "byStatus": dict(Counter(e.get("status", "unknown") for e in self.entries)),
            "hidden": sum(1 for e in self.entries if e.get("hidden")),
            "flagged": sum(1 for e in self.entries if any(e.get("flags", {}).values())),
        }


_MODEL = {"m": None, "tried": False}


def embed_model():
    """语义模型（可选）。只用本机已有的缓存，不联网下载；失败就返回 None，检索退回纯关键词。"""
    if not _MODEL["tried"]:
        _MODEL["tried"] = True
        try:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            from fastembed import TextEmbedding
            _MODEL["m"] = TextEmbedding(os.environ.get("AGENTSHELF_EMBED_MODEL", "BAAI/bge-small-zh-v1.5"))
        except Exception:
            _MODEL["m"] = None
    return _MODEL["m"]
