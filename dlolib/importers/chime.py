"""
导入 CHIME（MIT，https://github.com/yuboxie/chime）全部 1458 条，做成「中文互联网热梗」的打底条目。

- 读 seeds/chime/chime_full.json（随项目带，MIT，LICENSE 在同目录）。
- 每条的 brief（≤80 字短释义）是从原数据的释义里自动截的第一句，标 briefBy:"auto"；
  想换成自己的话，在管理页或命令行里改那一条，改过的条目重新导入时不会被覆盖。
- 冒犯 / 脏字只打标记（flags），不隐藏。
- 重复导入是安全的：没被人改过的会换成新的，改过的保留。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import SEEDS_DIR
from ..store import norm

DATASET = "CHIME"
IMPORTED_AT = "2026-10-30T00:00:00Z"        # 取数据的日期；固定下来，重导入结果才完全一样
SOURCE = {"kind": "seed", "by": "import", "dataset": DATASET, "license": "MIT",
          "url": "https://github.com/yuboxie/chime", "rev": "865ef18"}

# 少量手加的别名（常见的另一种说法 / 简写）
ALIASES = {
    "yyds": ["永远的神", "YYDS"],
    "尊嘟假嘟": ["尊嘟"],
    "泰裤辣": ["太酷啦"],
    "city不city": ["city不"],
    "依托答辩": ["一坨答辩"],
}


def brief_of(meme: str, meaning: str) -> str:
    """从原释义里截第一句当短释义（≤60 字）。"""
    m = (meaning or "").strip()
    m = re.sub(r'^[“"「]?' + re.escape(meme) + r'[”"」]?\s*', "", m)
    m = re.sub(r"^(是|指的是|指|意思是|一词|这个梗)", "", m)
    m = re.sub(r"^一个", "", m)
    first = re.split(r"[。！？]", m)[0]
    if len(first) > 60:
        cut = first.rfind("，", 0, 60)
        first = first[:cut] if cut > 15 else first[:58] + "…"
    return first.strip("，, ")


def era_of(origin: str) -> str:
    m = re.search(r"(20[012]\d)年", origin or "")
    return f"{m.group(1)}年前后" if m else ""


def load_raw(path=None) -> list:
    path = Path(path) if path else SEEDS_DIR / "chime" / "chime_full.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def merge_duplicates(raw: list) -> tuple:
    """CHIME 里有同名的两条（同一个梗写了两份释义）：留第一条，把第二条的例句补进来（最多 3 句）。"""
    seen, out, merged = {}, [], 0
    for x in raw:
        key = norm(x["meme"])
        if key in seen:
            first = seen[key]
            first["examples"] = (list(first.get("examples", [])) + list(x.get("examples", [])))[:3]
            merged += 1
        else:
            seen[key] = dict(x)
            out.append(seen[key])
    return out, merged


def build_entries(book, raw: list) -> list:
    out = []
    for x in raw:
        name = str(x["meme"]).strip()
        brief = brief_of(name, x.get("meaning")) or str(x.get("meaning") or "")[:60]
        if not name or not brief:
            continue
        out.append(book.build_entry(
            name=name, brief=brief, brief_by="auto", source=dict(SOURCE),
            aliases=ALIASES.get(name, []), meaning=x.get("meaning") or "",
            origin=(x.get("origin") or "")[:300], examples=x.get("examples", [])[:3],
            type=x.get("type_cn", ""), era=era_of(x.get("origin")), at=IMPORTED_AT,
            flags={k: True for k in ("profanity", "offense") if x.get(k)}))
    return out


def run(book, path=None) -> dict:
    raw = load_raw(path)
    unique, merged = merge_duplicates(raw)
    entries = build_entries(book, unique)
    res = book.import_entries(entries, DATASET)
    res["source"] = len(raw)
    res["mergedDuplicates"] = merged
    res["flagged"] = sum(1 for e in entries if e["flags"])
    return res
