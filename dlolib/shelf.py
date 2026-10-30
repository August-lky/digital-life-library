"""
书架：普通书（从头读到尾的那种）。想读 / 在读 / 读完了、读到哪、书签、每天读了多少。

不随项目附带任何书。只能导入两种：公版书（public-domain）和用户自己有的电子书（user-owned），
或者明确开放许可的（open-license）。核心从不联网下载书，文件由用户（或接入程序）自己放进来。

存在 <home>/shelf/shelf.json，正文转成纯文本放 <home>/shelf/texts/<id>.txt。
「读者」是任意字符串（agent id）。几个人一起读，就用一个共同的读者名（比如 together），进度共用。

书架管理（谁有权限由接入程序管，核心只提供动作）：改书名 / 作者 / 来源 / 许可说明 / 分类（edit）、挪位置（move）、
拿走（remove）和还原（restore）。拿走不是真删：书的记录（连同进度、书签）存进 <home>/shelf/trash.json，
正文挪进 <home>/shelf/trash/，想找回来 restore 一下就回到原来的样子。
"""
from __future__ import annotations

import hashlib
import html.parser
import json
import posixpath
import re
import shutil
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from .store import locked, now_iso, write_atomic

RIGHTS = {"public-domain": "公版", "user-owned": "自己有的", "open-license": "开放许可"}
READ_STATUS = {"want": "想读", "reading": "在读", "done": "读完了"}
MAX_BOOK_BYTES = 30 * 1024 * 1024      # 单本解压后上限，防压缩炸弹
DEFAULT_CHUNK = 1500
HISTORY_MAX = 20
EDIT_FIELDS = {"title": 80, "author": 60, "sourceUrl": 300, "license": 200, "category": 20}   # 能改的字段和长度上限


def today(tz_hours: float = 8) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=tz_hours)).strftime("%Y-%m-%d")


class _Text(html.parser.HTMLParser):
    BLOCK = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "section", "blockquote"}

    def __init__(self):
        super().__init__()
        self.out, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.out.append(data)


def html_to_text(s: str) -> str:
    p = _Text()
    p.feed(s)
    return "".join(p.out)


def read_epub(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        if sum(i.file_size for i in z.infolist()) > MAX_BOOK_BYTES:
            raise ValueError("这本书解压后太大了")
        container = ET.fromstring(z.read("META-INF/container.xml"))
        opf_path = next(el.get("full-path") for el in container.iter() if el.tag.endswith("rootfile"))
        opf = ET.fromstring(z.read(opf_path))
        base = posixpath.dirname(opf_path)
        manifest = {el.get("id"): el.get("href") for el in opf.iter() if el.tag.endswith("item")}
        parts = []
        for ref in (el for el in opf.iter() if el.tag.endswith("itemref")):
            href = manifest.get(ref.get("idref"))
            if href:
                name = posixpath.normpath(posixpath.join(base, href))
                parts.append(html_to_text(z.read(name).decode("utf-8", errors="replace")))
        return "\n".join(parts)


def load_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if path.stat().st_size > MAX_BOOK_BYTES:
        raise ValueError("文件太大了")
    if suffix == ".epub":
        text = read_epub(path)
    elif suffix in (".txt", ".md"):
        raw = path.read_bytes()
        for enc in ("utf-8-sig", "gb18030"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("认不出这个文件的编码（试过 UTF-8 和 GB18030）")
    elif suffix in (".html", ".htm", ".xhtml"):
        text = html_to_text(path.read_text(encoding="utf-8", errors="replace"))
    else:
        raise ValueError("只认 .txt .md .epub .html")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t　]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


class Shelf:
    def __init__(self, lib):
        self.lib = lib
        self.dir = lib.home / "shelf"
        self.file = self.dir / "shelf.json"
        self.trash_file = self.dir / "trash.json"
        (self.dir / "texts").mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict:
        if not self.file.exists():
            return {"books": []}
        with open(self.file, encoding="utf-8") as f:
            return json.load(f)

    def _save(self, data):
        write_atomic(self.file, json.dumps(data, ensure_ascii=False, indent=1))

    def _find(self, data, book: str):
        for b in data["books"]:
            if b["id"] == book or b["title"] == book:
                return b
        raise KeyError(f"书架上没有《{book}》")

    def _trash_load(self) -> dict:
        if not self.trash_file.exists():
            return {"books": []}
        with open(self.trash_file, encoding="utf-8") as f:
            return json.load(f)

    def _trash_save(self, data):
        write_atomic(self.trash_file, json.dumps(data, ensure_ascii=False, indent=1))

    def list(self, category: str = "") -> list:
        out = []
        for b in self._load()["books"]:
            if category and (b.get("category") or "") != category:
                continue
            out.append({k: b.get(k) for k in ("id", "title", "author", "rights", "chars", "status", "progress",
                                               "addedAt", "addedBy", "category", "sourceUrl", "license")}
                       | {"bookmarks": len(b.get("bookmarks", []))})
        return out

    def add(self, path, *, title: str, author: str = "", rights: str, by: str, source_url: str = "",
            category: str = "", license_note: str = "") -> dict:
        if rights not in RIGHTS:
            return {"ok": False, "error": f"rights 只能是 {'/'.join(RIGHTS)}（不收来路不明的书）"}
        path = Path(path)
        try:
            text = load_text(path)
        except (OSError, ValueError, KeyError, zipfile.BadZipFile, ET.ParseError, StopIteration) as e:
            return {"ok": False, "error": f"读不了这个文件：{e}"}
        book_id = "b-" + hashlib.sha1((title + "\n" + author).encode("utf-8")).hexdigest()[:10]
        with locked(self.file):
            data = self._load()
            if any(b["id"] == book_id for b in data["books"]):
                return {"ok": False, "error": f"《{title}》已经在书架上了"}
            if any(b["id"] == book_id for b in self._trash_load()["books"]):
                return {"ok": False, "error": f"《{title}》在回收站里，可以还原回来"}
            write_atomic(self.dir / "texts" / f"{book_id}.txt", text)
            b = {"id": book_id, "title": title, "author": author, "rights": rights, "sourceUrl": source_url,
                 "license": str(license_note or "")[:EDIT_FIELDS["license"]],
                 "category": str(category or "")[:EDIT_FIELDS["category"]],
                 "file": f"texts/{book_id}.txt", "chars": len(text), "addedAt": now_iso(), "addedBy": by,
                 "status": {}, "progress": {}, "bookmarks": [], "readLog": {}}
            data["books"].append(b)
            self._save(data)
            return {"ok": True, "book": b}

    # ── 书架管理：改信息 / 挪位置 / 拿走（进回收站）/ 还原 ──
    def edit(self, book: str, fields: dict, by: str) -> dict:
        """改书名 / 作者 / 来源网址 / 许可说明 / 分类。书的 id 不变（进度、书签都跟着）。"""
        bad = set(fields) - set(EDIT_FIELDS)
        if bad:
            return {"ok": False, "error": f"这些不能改：{'、'.join(sorted(bad))}"}
        clean = {}
        for k, v in fields.items():
            v = str(v if v is not None else "").strip()
            if len(v) > EDIT_FIELDS[k]:
                return {"ok": False, "error": f"{k} 太长了，{EDIT_FIELDS[k]} 字以内"}
            if k == "title" and not v:
                return {"ok": False, "error": "书名不能空"}
            if k == "sourceUrl" and v and not re.match(r"^https?://", v, re.I):
                return {"ok": False, "error": "来源要写网址（http 或 https 开头），没有就留空"}
            clean[k] = v
        with locked(self.file):
            data = self._load()
            b = self._find(data, book)
            changed = {k: v for k, v in clean.items() if (b.get(k) or "") != v}
            if not changed:
                return {"ok": True, "book": b, "note": "没有变化"}
            t, a = changed.get("title", b["title"]), changed.get("author", b.get("author", ""))
            if any(x is not b and x["title"] == t and (x.get("author") or "") == a for x in data["books"]):
                return {"ok": False, "error": f"书架上已经有一本《{t}》了"}
            at = now_iso()
            before = {k: b.get(k, "") for k in changed}
            b.update(changed)
            b.setdefault("history", []).append({"at": at, "by": by, "op": "edit", "fields": sorted(changed),
                                                "before": before})
            b["history"] = b["history"][-HISTORY_MAX:]
            self._save(data)
            return {"ok": True, "book": b}

    def move(self, book: str, category=None, position=None) -> dict:
        """换分类（category，空字符串 = 不分类）和/或挪位置（position：从 1 数起的名次，或 top / bottom）。"""
        if category is None and position is None:
            return {"ok": False, "error": "要给 category 或 position"}
        if category is not None and len(str(category).strip()) > EDIT_FIELDS["category"]:
            return {"ok": False, "error": f"分类名太长了，{EDIT_FIELDS['category']} 字以内"}
        with locked(self.file):
            data = self._load()
            b = self._find(data, book)
            if position is not None:
                books = data["books"]
                pos = str(position).strip().lower()
                if pos in ("top", "first"):
                    idx = 0
                elif pos in ("bottom", "last"):
                    idx = len(books) - 1
                else:
                    try:
                        idx = max(1, min(int(pos), len(books))) - 1
                    except ValueError:
                        return {"ok": False, "error": "position 写名次（1、2、3……）或 top / bottom"}
                books.remove(b)
                books.insert(idx, b)
            if category is not None:
                b["category"] = str(category).strip()
            self._save(data)
            order = [x["id"] for x in data["books"]]
            return {"ok": True, "book": b["title"], "category": b.get("category", ""),
                    "position": order.index(b["id"]) + 1, "total": len(order)}

    def remove(self, book: str, by: str) -> dict:
        """把一本书拿下书架。不是真删：记录（连同进度、书签）进 trash.json，正文挪进 trash/，restore 能原样还原。"""
        with locked(self.file):
            data = self._load()
            b = self._find(data, book)
            trash = self._trash_load()
            if any(x["id"] == b["id"] for x in trash["books"]):
                return {"ok": False, "error": f"回收站里已经有一本同样的《{b['title']}》了，先处理那一本"}
            src = self.dir / b["file"]
            (self.dir / "trash").mkdir(parents=True, exist_ok=True)
            if src.exists():
                shutil.copy2(src, self.dir / "trash" / f"{b['id']}.txt")     # 先拷过去、记录都写好了再删原件，中途出事不会丢书
            data["books"] = [x for x in data["books"] if x is not b]
            trash["books"].append({**b, "removedAt": now_iso(), "removedBy": by})
            self._trash_save(trash)
            self._save(data)
            if src.exists():
                src.unlink()
            return {"ok": True, "removed": b["title"], "id": b["id"]}

    def trash(self) -> list:
        return [{k: b.get(k) for k in ("id", "title", "author", "chars", "removedAt", "removedBy", "category")}
                for b in self._trash_load()["books"]]

    def restore(self, book: str) -> dict:
        with locked(self.file):
            trash = self._trash_load()
            hit = next((x for x in trash["books"] if x["id"] == book or x["title"] == book), None)
            if hit is None:
                return {"ok": False, "error": f"回收站里没有《{book}》"}
            data = self._load()
            if any(x["id"] == hit["id"] for x in data["books"]):
                return {"ok": False, "error": f"书架上已经有《{hit['title']}》了"}
            src = self.dir / "trash" / f"{hit['id']}.txt"
            if not src.exists():
                return {"ok": False, "error": f"《{hit['title']}》的正文在回收站里找不到了"}
            shutil.copy2(src, self.dir / hit["file"])
            back = {k: v for k, v in hit.items() if k not in ("removedAt", "removedBy")}
            data["books"].append(back)
            trash["books"] = [x for x in trash["books"] if x is not hit]
            self._save(data)
            self._trash_save(trash)
            src.unlink()
            return {"ok": True, "book": back["title"], "id": back["id"]}

    def set_status(self, book: str, reader: str, status: str) -> dict:
        if status not in READ_STATUS:
            return {"ok": False, "error": f"status 只能是 {'/'.join(READ_STATUS)}"}
        with locked(self.file):
            data = self._load()
            b = self._find(data, book)
            b["status"][reader] = status
            self._save(data)
            return {"ok": True, "book": b["title"], "reader": reader, "status": status}

    def daily_limit(self, who: str):
        """每人每天最多读多少字。dailyChars 可以是一个数（所有人一样），也可以是 {"default": N, "某人": M}。没设 = 不限。"""
        cfg = (self.lib.config.get("shelf", {}) or {}).get("dailyChars")
        if isinstance(cfg, dict):
            cfg = cfg.get(who, cfg.get("default"))
        try:
            return int(cfg) if cfg else None
        except (TypeError, ValueError):
            return None

    def read(self, book: str, reader: str, chars: int = DEFAULT_CHUNK, restart: bool = False,
             charge: str | None = None) -> dict:
        """从 reader 上次读到的地方往下读一段（停在段落结尾），进度和今天读了多少字都记下来。

        charge：今天读的字算在谁头上（默认就是 reader）。几个人「一起读」时进度共用同一个读者名，
        但每个人每天读了多少还是各算各的，所以调用方把 charge 写成真正在读的那个人。"""
        payer = charge or reader
        limit = self.daily_limit(payer)
        with locked(self.file):
            data = self._load()
            b = self._find(data, book)
            day = today()
            done_today = sum(x.get("readLog", {}).get(day, {}).get(payer, 0)       # 今天所有书加起来（回收站里的也算，拿走一本不会让额度重来）
                             for x in data["books"] + self._trash_load()["books"])
            if limit and done_today >= limit:
                return {"ok": False, "error": "今天读得不少了，明天接着读吧", "todayChars": done_today, "limitReached": True}
            text = (self.dir / b["file"]).read_text(encoding="utf-8")
            pos = 0 if restart else int(b["progress"].get(reader, 0))
            chars = max(200, min(int(chars or DEFAULT_CHUNK), 6000))
            if limit:
                chars = min(chars, max(200, limit - done_today))
            end = min(len(text), pos + chars)
            nl = text.rfind("\n", pos + chars // 2, end + 200) if end < len(text) else -1
            if nl > pos:
                end = nl + 1
            piece = text[pos:end]
            b["progress"][reader] = end
            for who in {reader, payer}:                  # 一起读的时候，读的人自己的状态也跟着走
                b["status"][who] = "done" if end >= len(text) else "reading"      # 想读的书读起来就变成在读
            log = b["readLog"].setdefault(day, {})
            log[payer] = log.get(payer, 0) + len(piece)
            self._save(data)
            return {"ok": True, "title": b["title"], "text": piece, "from": pos, "to": end, "total": len(text),
                    "percent": round(100 * end / max(1, len(text)), 1), "finished": end >= len(text),
                    "todayChars": done_today + len(piece)}

    def bookmark(self, book: str, reader: str, note: str = "", pos: int | None = None) -> dict:
        note = str(note or "").strip()[:200]
        with locked(self.file):
            data = self._load()
            b = self._find(data, book)
            p = int(b["progress"].get(reader, 0) if pos is None else pos)
            text = (self.dir / b["file"]).read_text(encoding="utf-8")
            quote = text[max(0, p - 40):p].strip()[-40:]
            mark = {"at": now_iso(), "by": reader, "pos": p, "note": note, "quote": quote}
            b["bookmarks"].append(mark)
            self._save(data)
            return {"ok": True, "book": b["title"], "bookmark": mark}

    def bookmarks(self, book: str, reader: str = "") -> list:
        b = self._find(self._load(), book)
        return [m for m in b["bookmarks"] if not reader or m["by"] == reader]

    def reading_log(self, days: int = 7) -> dict:
        out = {}
        for b in self._load()["books"]:
            for day, who in b.get("readLog", {}).items():
                for r, n in who.items():
                    out.setdefault(day, {}).setdefault(r, 0)
                    out[day][r] += n
        return dict(sorted(out.items())[-days:])
