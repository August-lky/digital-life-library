"""
公开导出 + 发布前隐私扫描。

导出做三件事：
  1. 只带许可白名单里的打底数据（默认：MIT、CC BY-SA 4.0、CC0）。每个许可一个文件，不混在一起——
     CC BY-SA 的数据要用同样的许可发布，和 MIT 的分开才不会互相污染。
  2. 剥掉隐私字段：谁存的（by，import 除外）、在哪碰到的（where）、note、改动记录（history）、被隐藏的条目。
     她们存的、主人手填的条目默认不带；要带就加 --include-user-entries 并写明许可，且只留名字 / 释义 / 出处。
  3. 用「私有词表」（群名、人名、号码之类，只放本机、不进仓库）加上通用规则（手机号、邮箱、内网地址、
     本机路径、密钥格式）把要导出的内容扫一遍。有一处命中就整个拒绝导出，什么都不写；
     报告只给位置，不回显命中的原文。

scan_tree() 用同样的规则扫一个目录（比如准备推送的仓库），发布前跑一遍。
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import unicodedata
from pathlib import Path

from .importers import SEEDS_DIR
from .store import PRIVATE_RE, now_iso

# 导出许可白名单：写在条目 source.license 里的值 → 文件名里用的短名
LICENSE_SLUGS = {"MIT": "mit", "CC BY-SA 4.0": "cc-by-sa-4.0", "CC0-1.0": "cc0"}
KEEP_SOURCE = ("kind", "by", "dataset", "license", "url", "rev", "page", "revision", "list", "year")
USER_KEEP = ("id", "book", "name", "aliases", "brief", "origin", "examples", "type", "era", "status", "flags")

# 内网地址：10.x.x.x、192.168.x.x、172.16～31.x.x
PRIVATE_NET = (r"10(?:\.\d{1,3}){3}", r"192\.168(?:\.\d{1,3}){2}", r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}")
GENERIC_PATTERNS = [
    (re.compile(r"(?<![0-9A-Za-z])(?:" + "|".join(PRIVATE_NET) + r")(?![0-9])"), "内网地址"),
    (re.compile(r"[A-Za-z]:\\Users\\[^\\\s\"']{2,}", re.I), "本机路径"),
    (re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}"), "密钥格式（sk-）"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"), "GitHub 令牌"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "私钥"),
    (re.compile(r"(?<![A-Za-z0-9])[0-9a-f]{48}(?![A-Za-z0-9])"), "令牌格式（48 位十六进制）"),
    (re.compile(r"(?<![\d\w/.=-])[1-9]\d{8,10}(?![\d\w.-])"), "像 QQ 号 / 群号的长数字"),
    (PRIVATE_RE, "手机号 / 身份证 / 邮箱 / 带号码的 QQ 微信"),
]
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".jsonl", ".html", ".toml", ".cfg", ".yml", ".yaml", ".js", ".mjs", ""}
SKIP_DIRS = {".git", "__pycache__", ".index", ".cache", "node_modules", ".trash-1030"}
# 这几处本来就是在「定义 / 测试」个人信息的匹配规则，里面会出现手机号、内网地址的样子（测试里的是假的）：
# 只查私有词表，不套通用规则
GENERIC_EXEMPT = ("dlolib/store.py", "dlolib/export.py", "tests/")


def _fold(s: str) -> str:
    return unicodedata.normalize("NFKC", s).lower()


def load_words(files) -> list:
    """读私有词表。每行一个词（# 开头是注释），也认 JSON 数组。没文件就是空表。"""
    words = []
    for f in files or []:
        p = Path(f)
        if not p.exists():
            raise FileNotFoundError(f"找不到词表文件：{p.name}")
        text = p.read_text(encoding="utf-8")
        if text.lstrip().startswith("["):
            items = json.loads(text)
        else:
            items = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
        words += [str(w) for w in items]
    return sorted({w.strip() for w in words if len(w.strip()) >= 2}, key=len, reverse=True)


def scan_text(text: str, words: list, allow: list | None = None, generic: bool = True) -> list:
    """返回命中的种类列表（不含原文）：['私有词#17', '内网地址', …]。"""
    hits = []
    allowed = {_fold(a) for a in (allow or [])}
    folded = _fold(text)
    for i, w in enumerate(words):
        fw = _fold(w)
        if fw in allowed:
            continue
        if fw in folded:
            hits.append(f"私有词#{i}")
    if generic:
        for rx, label in GENERIC_PATTERNS:
            if rx.search(text):
                hits.append(label)
    return hits


def third_party_corpus(root=None) -> str:
    """别人写的公开原始数据的全文（CHIME 原文件、维基页面缓存）。私有词表里的词如果在这些文字里本来就有，
    说明它是个普通词（或者本来就是公开的），出现在由这些数据导出来的条目里不算泄露。"""
    base = Path(root) if root else SEEDS_DIR.parent
    files = [base / "seeds" / "chime" / "chime_full.json", *sorted((base / "seeds" / "wikipedia" / ".cache").glob("*.json"))]
    return "".join(_fold(f.read_text(encoding="utf-8")) for f in files if f.exists())


def relaxed_words(words: list, corpus: str) -> list:
    return [w for w in words if _fold(w) not in corpus]


def scan_tree(path, words: list, allow: list | None = None) -> list:
    """扫目录里的文本文件，返回 ['相对路径:行号 命中种类', …]。不回显命中的原文。
    seeds/ 目录里的文件用「去掉了公开原始数据里本来就有的普通词」的词表，其余文件用完整词表。"""
    root = Path(path)
    hits = []
    corpus = third_party_corpus(root if root.is_dir() else root.parent)
    seeds_words = relaxed_words(words, corpus) if corpus else words
    files = [root] if root.is_file() else [p for p in sorted(root.rglob("*")) if p.is_file()]
    for p in files:
        rel = p.relative_to(root.parent if root.is_file() else root)
        if any(part in SKIP_DIRS for part in rel.parts) or p.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if p.stat().st_size > 20 * 1024 * 1024:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            hits.append(f"{rel} 不是 UTF-8 文本，没法扫")
            continue
        use = seeds_words if rel.parts and rel.parts[0] == "seeds" else words
        generic = not rel.as_posix().startswith(GENERIC_EXEMPT)
        for n, line in enumerate(text.splitlines(), 1):
            for kind in scan_text(line, use, allow, generic):
                hits.append(f"{rel}:{n} {kind}")
    return hits


def _public_entry(e: dict) -> dict:
    out = {k: v for k, v in e.items() if k != "history"}
    out["source"] = {k: v for k, v in e.get("source", {}).items() if k in KEEP_SOURCE}
    out["history"] = [{"at": e.get("createdAt", ""), "by": "import", "op": "add"}]
    out["hidden"] = False
    return out


def _user_entry(e: dict, license_: str) -> dict:
    out = {k: e[k] for k in USER_KEEP if k in e}
    out["source"] = {"kind": "community", "license": license_}
    out["createdAt"] = out["updatedAt"] = e.get("createdAt", "")
    return out


def export(lib, out_dir, *, private_words=None, allow_words=None, include_user_entries=False,
           user_license="", book_id="") -> dict:
    private_words = private_words or []
    if include_user_entries and not user_license:
        return {"ok": False, "error": "要带她们存的 / 手填的条目，请用 --user-entries-license 写明许可（比如 CC0-1.0）"}
    book = lib.book(book_id)
    corpus = third_party_corpus()
    relaxed = relaxed_words(private_words, corpus) if corpus else private_words
    groups, skipped, hits = {}, {"hidden": 0, "licenseNotAllowed": 0, "userEntries": 0}, []
    for e in book.entries:
        s = e.get("source", {})
        if e.get("hidden"):
            skipped["hidden"] += 1
            continue
        if s.get("kind") == "seed":
            if s.get("license") not in LICENSE_SLUGS:
                skipped["licenseNotAllowed"] += 1
                continue
            slug, row = LICENSE_SLUGS[s["license"]], _public_entry(e)
            # 没被人改过的打底条目：私有词表里「公开原始数据本来就有」的普通词不算命中；人改过的按完整词表查
            pristine = all(h.get("by") == "import" for h in e.get("history", []))
            use = relaxed if pristine else private_words
        elif include_user_entries:
            slug, row, use = "community-" + re.sub(r"[^a-z0-9]+", "-", user_license.lower()), _user_entry(e, user_license), private_words
        else:
            skipped["userEntries"] += 1
            continue
        rows = groups.setdefault(slug, [])
        for kind in scan_text(json.dumps(row, ensure_ascii=False), use, allow_words):
            hits.append(f"entries.{slug}.jsonl:{len(rows) + 1} {kind}")
        rows.append(row)

    # 先在临时目录写好、扫完，没问题才挪到目标位置
    tmp = Path(tempfile.mkdtemp(prefix="dlolib-export-"))
    try:
        files = {}
        for slug, rows in groups.items():
            f = tmp / book.id / f"entries.{slug}.jsonl"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8", newline="\n")
            files[f"{book.id}/{f.name}"] = len(rows)
        manifest = {"book": book.id, "generatedAt": now_iso(), "files": files, "skipped": skipped,
                    "note": "不同许可的数据分开放，使用时请遵守各自的许可（见 DATA_LICENSES.md）。"}
        (tmp / "EXPORT_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                                  encoding="utf-8", newline="\n")
        hits += [h for h in scan_tree(tmp / "EXPORT_MANIFEST.json", private_words, allow_words)]
        if hits:
            return {"ok": False, "error": f"隐私扫描有 {len(hits)} 处命中，没有导出任何东西", "hits": hits[:50]}
        out = Path(out_dir)
        if out.exists() and any(out.iterdir()):
            return {"ok": False, "error": "目标目录不是空的，换一个新目录"}
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(tmp, out, dirs_exist_ok=True)
        return {"ok": True, "out": str(out), "files": files, "skipped": skipped, "scanned": "干净",
                "privateWordsUsed": len(private_words)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
