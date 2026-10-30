"""
2026 年网络热梗盘点（1 月到 10 月出现或爆火的热梗、黑话）。

和年度榜单一样：梗名、时间、来龙去脉是事实；释义和出处说明全部是本项目自己写的（seeds/y2026/y2026_memes.json），
所以这份数据按 CC0-1.0 发布。每条的 sources 是写这一条所依据的报道链接，会变成条目的 refs。
政治、时政类、涉及真实普通人隐私的、侮辱特定群体的不收；歌词不收（成了梗的一两句、登记在歌词白名单里的除外，见 lyrics.py）。

接龙类（上句 → 下句）多两个字段：prev（上句）、next（下句），type 是「接龙梗」；
接龙的上下句如果是歌词，type 是「歌词梗」，lyric 写白名单里的歌曲 id，导入时由 store 层校验。

导入时：
  1. 书里还没有的梗：新增一条，来源 dataset=2026年盘点，license=CC0-1.0，by=import；
  2. 书里已经有的（alsoReFired 里列的）：只补一条「2026 年又火了」的 ref，不动原来的释义和流行时间；
  3. 重复导入是安全的：没被人改过的会换成新的，改过的保留。
建议顺序：chime、wikipedia、yearly、y2026（重新导入前面的会冲掉这里补的 ref，再跑一遍 y2026 即可）。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import SEEDS_DIR
from ..store import now_iso

DATASET = "2026年盘点"
LICENSE = "CC0-1.0"
REF_KIND = "y2026"


def load(path=None) -> dict:
    p = Path(path) if path else SEEDS_DIR / "y2026" / "y2026_memes.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _refs(urls: list, year: int = 2026, kind: str = REF_KIND) -> list:
    return [{"kind": kind, "year": year, "url": u} for u in urls]


def run_seed(book, data: dict, *, dataset: str = DATASET, license: str = LICENSE,
             ref_kind: str = REF_KIND, default_year: int = 2026) -> dict:
    """按「梗 seed」的格式导入。2026 盘点和 jjk 等主题 seed 共用；每个 seed 自己的 dataset 名，互不冲掉。
    条目可以带自己的 year（没有就用 default_year），alsoReFired 里的 ref 同理。"""
    at = data.get("retrieved", "2026-10-30") + "T00:00:00Z"
    book.import_entries([], dataset)               # 先清掉上一次导进来的（没被人改过的），重跑不会多也不会少
    new_entries = []                                # 名字或别名撞上别的来源的，import_entries 会跳过并记在 skipped 里
    for t in data["terms"]:
        year = t.get("year", default_year)
        src = {"kind": "seed", "by": "import", "dataset": dataset, "license": license,
               "url": (t.get("sources") or [""])[0], "year": year}
        if t.get("maintainer"):
            src["note"] = t["maintainer"]            # 老大 / 维护者口头补充的（没有链接可附）
        e = book.build_entry(
            name=t["name"], aliases=t.get("aliases", []), brief=t["brief"], source=src, at=at,
            meaning=t.get("meaning", ""), origin=t.get("origin", ""), era=t.get("era", ""),
            type=t.get("type", ""), flags=t.get("flags", {}), status=t.get("status"),
            refs=_refs(t.get("sources", []), year, ref_kind)
            + ([{"kind": ref_kind, "year": year, "note": t["maintainer"]}] if t.get("maintainer") else []))
        for k in ("prev", "next", "topic", "confidence", "lyric"):
            if t.get(k):
                e[k] = t[k]
        new_entries.append(e)
    res = book.import_entries(new_entries, dataset)

    refreshed, missing = 0, []
    for r in data.get("alsoReFired", []):
        e = book.find_by_name(r["name"])
        if not e:
            missing.append(r["name"])
            continue
        keep = [x for x in e.get("refs", []) if x.get("kind") != ref_kind]
        add = [{"kind": ref_kind, "year": r.get("year", default_year), "note": r.get("note", ""), "url": u}
               for u in r.get("sources", [])]
        out = book.update(e["id"], {"refs": keep + add}, "import")
        refreshed += 1 if out["ok"] else 0
    return {"ok": True, "terms": len(data["terms"]), "created": res["added"], "skipped": res["skipped"],
            "rejected": res["rejected"], "alsoReFired": refreshed, "alsoReFiredMissing": missing, "generatedAt": now_iso()}


def run(book, path=None) -> dict:
    return run_seed(book, load(path))
