"""
年度榜单（咬文嚼字十大流行语、国家语言资源监测与研究中心十大网络用语、B站年度弹幕）。

只收「词条名 + 年份 + 榜单名」，这些是事实；短释义是本项目自己写的（seeds/yearly/yearly_lists.json 的 briefs），
所以这份数据按 CC0-1.0 发布。政治、政策、时政类词条整条不收。

导入时做两件事：
  1. 书里已经有的词（CHIME、维基导进来的）：补上「上过哪个榜、哪一年」（refs），
     流行时间 era 空着就填首次上榜的年份，heat 记上榜次数。已有的释义不动。
  2. 书里没有、而且我们写了释义的词：新增一条，来源 dataset=年度榜单，license=CC0-1.0。
     没有写释义的词只记榜单，不凑条目。
建议顺序：先 chime、再 wikipedia、最后 yearly（重新导入 chime 会把这里补的 refs 冲掉，再跑一遍 yearly 即可）。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import SEEDS_DIR
from .wikipedia import split_names
from ..store import now_iso, norm

DATASET = "年度榜单"
LICENSE = "CC0-1.0"


def load(path=None) -> dict:
    p = Path(path) if path else SEEDS_DIR / "yearly" / "yearly_lists.json"
    return json.loads(p.read_text(encoding="utf-8"))


def collect(data: dict) -> dict:
    """{规范化后的名字: {"name": 原名, "appearances": [(榜单, 年份, 来源网址)]}}"""
    out = {}
    for lst in data["lists"]:
        for term in lst["terms"]:
            name, aliases = split_names(term)
            keys = [norm(n) for n in [name, *aliases] if norm(n)]
            if not keys:
                continue
            rec = out.setdefault(keys[0], {"name": name, "aliases": aliases, "term": term, "appearances": []})
            rec["appearances"].append((lst["list"], lst["year"], (lst.get("sources") or [""])[0]))
    return out


def run(book, path=None) -> dict:
    data = load(path)
    terms = collect(data)
    briefs = data.get("briefs", {})
    updated, created, listed_only = 0, 0, []
    book.import_entries([], DATASET)          # 先清掉上一次导进来的榜单词条（没被人改过的），重跑不会少也不会多
    at = data.get("retrieved", "2026-10-30") + "T00:00:00Z"
    new_entries = []
    for key, rec in terms.items():
        refs = [{"kind": "yearly-list", "list": lst, "year": year, "url": url} for lst, year, url in rec["appearances"]]
        years = sorted({y for _, y, _ in rec["appearances"]})
        e = book.find_by_name(rec["name"]) or next((x for a in rec["aliases"] if (x := book.find_by_name(a))), None)
        if e:
            fields = {"refs": [r for r in e.get("refs", []) if r.get("kind") != "yearly-list"] + refs,
                      "heat": float(len({(lst, y) for lst, y, _ in rec["appearances"]}))}
            if not e.get("era"):
                fields["era"] = f"{years[0]}年上榜"
            res = book.update(e["id"], fields, "import")
            updated += 1 if res["ok"] else 0
            continue
        brief = briefs.get(rec["term"]) or briefs.get(rec["name"])
        if not brief:
            listed_only.append(rec["name"])
            continue
        src = {"kind": "seed", "by": "import", "dataset": DATASET, "license": LICENSE,
               "url": rec["appearances"][0][2], "list": rec["appearances"][0][0], "year": years[0]}
        new_entries.append(book.build_entry(
            name=rec["name"], aliases=rec["aliases"], brief=brief, source=src, refs=refs, at=at,
            era=f"{years[0]}年上榜", heat=float(len({(lst, y) for lst, y, _ in rec["appearances"]})), type="榜单词"))
    res = book.import_entries(new_entries, DATASET)
    created = res["added"]
    return {"ok": True, "lists": len(data["lists"]), "distinctTerms": len(terms), "updatedExisting": updated,
            "created": created, "skipped": res["skipped"], "rejected": res["rejected"], "listedOnly": listed_only, "generatedAt": now_iso()}
