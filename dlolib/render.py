"""
把查到的条目写成给 AI（或人）看的短文字：先说意思，再说出处和一句例句，有标记的提醒一句。
命令行、MCP、HTTP 都用这里，格式只有一处。说明文字用家常话，写「可以」，不催人。
"""
from __future__ import annotations

from .store import STATUSES

NOT_FOUND = "书里没找到「{q}」。可以搜一下，弄懂了存一句进来，下次就有了。"


def clip(s, n: int) -> str:
    s = str(s or "").replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def source_line(lib, e: dict) -> str:
    s = e.get("source", {})
    kind = lib.label(s.get("kind", ""))
    if s.get("kind") == "seed":
        return f"{kind}：{s.get('dataset', '')}"
    who = lib.agent_name(s.get("by", ""))
    where = f"，在{s['where']}碰到" if s.get("where") else ""
    return f"{kind}：{who}存的{where}"


def flag_note(e: dict) -> str:
    fl = e.get("flags", {}) or {}
    if not (fl.get("offense") or fl.get("profanity")):
        return ""
    parts = []
    if fl.get("profanity"):
        parts.append("带脏字")
    if fl.get("offense"):
        parts.append("可能冒犯人")
    return "注意：这个梗" + "、".join(parts) + "，接的时候看场合"


def render_entry(lib, e: dict, score=None, why: str = "") -> str:
    head = f"「{e['name']}」"
    tags = [t for t in (e.get("type"), e.get("era")) if t]
    st = e.get("status")
    if st in ("dated", "fading"):
        tags.append(STATUSES[st])
    if tags:
        head += f"（{'，'.join(tags)}）"
    lines = [f"{head}：{e.get('brief') or e.get('meaning')}"]
    if e.get("aliases"):
        lines.append(f"  也写作：{'、'.join(e['aliases'])}")
    if e.get("next"):
        lines.append(f"  接龙：{e.get('prev') or e['name']} → {e['next']}")
    ly = e.get("lyric")
    if isinstance(ly, dict) and ly.get("song"):
        lines.append(f"  这句是歌词：{ly.get('artist', '')}《{ly['song']}》里的，因为成了梗才收；只收这一两句")
    if e.get("origin"):
        lines.append(f"  出处：{clip(e['origin'], 90)}")
    if e.get("examples"):
        lines.append(f"  例句：{clip(e['examples'][0], 50)}")
    note = flag_note(e)
    if note:
        lines.append("  " + note)
    tail = f"  来源：{source_line(lib, e)}"
    if score is not None:
        tail += f"　（{why}，{score}）"
    lines.append(tail)
    return "\n".join(lines)


def render_search(lib, query: str, results: list) -> str:
    if not results:
        return NOT_FOUND.format(q=query)
    return "\n".join(render_entry(lib, r["entry"], r["score"], r["why"]) for r in results)


BROWSE_MODE_NAME = {"topic": "按话题", "source": "按来源", "recent": "最近新增", "random": "随便翻翻"}


def render_browse(lib, res: dict) -> str:
    """逛一逛的结果：每条一行（名字、类型、半句意思），最后告诉怎么翻下一页。要短。"""
    if not res.get("ok"):
        return "没翻成：" + res.get("error", "")
    mode = res["mode"]
    if "menu" in res:
        what = "话题（类型）" if mode == "topic" else "来源"
        names = "、".join(f"{n}（{c}）" for n, c in res["menu"]) or "还没有"
        return f"{what}有：{names}。挑一个当 value 再翻。"
    items = res["items"]
    if not items:
        return f"这一页没有了（{BROWSE_MODE_NAME[mode]}{('：' + res['value']) if res.get('value') else ''}，共 {res['total']} 条）。"
    head = f"{BROWSE_MODE_NAME[mode]}{('：' + res['value']) if res.get('value') else ''}"
    head += f"，随手翻到 {len(items)} 条" if mode == "random" else f"，共 {res['total']} 条，第 {res['page']}/{res['pages']} 页"
    lines = [head]
    for e in items:
        tag = f"[{e['type']}]" if e.get("type") else ""
        flag = "（可能冒犯人）" if (e.get("flags") or {}).get("offense") else ""
        lines.append(f"· 「{e['name']}」{tag}{clip(e.get('brief') or e.get('meaning'), 36)}{flag}")
    if mode != "random" and res["page"] < res["pages"]:
        lines.append(f"接着翻：page={res['page'] + 1}")
    lines.append("想看某一条的全部，用 library_lookup 查它的名字。")
    return "\n".join(lines)


def render_scan(entries: list) -> str:
    return "\n".join(f"（这句话里有梗：「{e['name']}」—— {clip(e.get('brief'), 40)}）" for e in entries) or "（没有）"
