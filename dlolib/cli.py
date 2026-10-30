"""
命令行。用法：python -m dlolib <命令> --home <图书馆目录> ...

  init                        建一个新的图书馆目录（可加 --seed chime 顺便导入打底数据）
  find "尊嘟假嘟"             查（--semantic 连意思一起找）
  scan "这家店也太city了吧"    被动模式：一句话里有没有书里的梗
  show <id> / list / stats    看一条 / 列表（可按来源、谁存的、状态筛）/ 统计
  add / update / mark / remove  增改标记拿走（--by 写清是谁；老大手填用 --kind owner）
  import chime|wikipedia|yearly   导入随项目带的打底数据（不联网）
  fetch-wikipedia             联网读维基「中国大陆网络用语列表」（慢速、只读、2 个请求），原文只存本机缓存
  build-wikipedia-seed        用缓存里的词条名 + briefs.json 里自己写的释义，生成 wikipedia_netslang.json
  shelf ...                   书架：add / list / read / status / bookmark / bookmarks / log
  serve                       HTTP 接口 + 管理页（127.0.0.1:3212）
  mcp --agent <id>            stdio MCP 服务，--agent 固定「谁存的」
  export --out DIR            公开导出（按许可过滤、剥隐私字段、用私有词表扫一遍）
  scan-tree PATH              用私有词表扫一个目录（发布前检查）

--home 默认读环境变量 DLOLIB_HOME，没有就用 ~/.digital-life-library。--json 输出原始 JSON。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import importers, render
from .store import Library, STATUSES


def out_json(o):
    print(json.dumps(o, ensure_ascii=False, indent=1))


def default_home() -> Path:
    return Path(os.environ.get("DLOLIB_HOME") or Path.home() / ".digital-life-library")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="dlolib", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", default="", help="图书馆目录（默认 $DLOLIB_HOME 或 ~/.digital-life-library）")
    ap.add_argument("--book", default="", help="哪本工具书（默认第一本）")
    ap.add_argument("--json", action="store_true", help="输出原始 JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.add_argument("--owner-name", default="主人")
    p.add_argument("--seed", action="append", default=[], choices=importers.IMPORTERS)

    p = sub.add_parser("find")
    p.add_argument("query")
    p.add_argument("-k", type=int, default=3)
    p.add_argument("--semantic", action="store_true")

    p = sub.add_parser("scan")
    p.add_argument("message")

    p = sub.add_parser("show")
    p.add_argument("id")

    p = sub.add_parser("list")
    p.add_argument("--source", default="")
    p.add_argument("--by", default="")
    p.add_argument("--status", default="")
    p.add_argument("-q", default="")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--offset", type=int, default=0)

    sub.add_parser("stats")

    p = sub.add_parser("add")
    p.add_argument("--name", required=True)
    p.add_argument("--brief", required=True)
    p.add_argument("--alias", action="append", default=[])
    p.add_argument("--origin", default="")
    p.add_argument("--example", action="append", default=[])
    p.add_argument("--kind", required=True, choices=("asked", "self", "owner"))
    p.add_argument("--by", default="")
    p.add_argument("--where", default="")
    p.add_argument("--url", default="")
    p.add_argument("--note", default="")

    p = sub.add_parser("update")
    p.add_argument("id")
    p.add_argument("--set", action="append", default=[], metavar="字段=值", help="例：brief=新的释义；aliases=a,b")
    p.add_argument("--by", default="owner")

    p = sub.add_parser("mark")
    p.add_argument("id")
    p.add_argument("--status", choices=tuple(STATUSES))
    p.add_argument("--hidden", choices=("1", "0"))
    p.add_argument("--by", default="owner")

    p = sub.add_parser("remove")
    p.add_argument("id")
    p.add_argument("--by", default="owner")

    p = sub.add_parser("import")
    p.add_argument("dataset", choices=importers.IMPORTERS)
    p.add_argument("--path", default="", help="用别的数据文件（默认用随项目带的）")

    p = sub.add_parser("fetch-wikipedia")
    p.add_argument("--out", default="")
    p.add_argument("--delay", type=float, default=3.0, help="两次请求之间隔几秒（默认 3）")

    sub.add_parser("build-wikipedia-seed")

    p = sub.add_parser("shelf")
    ssub = p.add_subparsers(dest="shelf_cmd", required=True)
    s = ssub.add_parser("add")
    s.add_argument("path")
    s.add_argument("--title", required=True)
    s.add_argument("--author", default="")
    s.add_argument("--rights", required=True, choices=("public-domain", "user-owned", "open-license"))
    s.add_argument("--by", default="owner")
    s.add_argument("--url", default="")
    ssub.add_parser("list")
    s = ssub.add_parser("read")
    s.add_argument("book")
    s.add_argument("--reader", default="owner")
    s.add_argument("--chars", type=int, default=1500)
    s.add_argument("--restart", action="store_true")
    s = ssub.add_parser("status")
    s.add_argument("book")
    s.add_argument("status", choices=("want", "reading", "done"))
    s.add_argument("--reader", default="owner")
    s = ssub.add_parser("bookmark")
    s.add_argument("book")
    s.add_argument("--reader", default="owner")
    s.add_argument("--note", default="")
    s = ssub.add_parser("bookmarks")
    s.add_argument("book")
    s.add_argument("--reader", default="")
    s = ssub.add_parser("log")
    s.add_argument("--days", type=int, default=7)

    p = sub.add_parser("serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=3212)

    p = sub.add_parser("mcp")
    p.add_argument("--agent", required=True, help="固定「谁存的」，不让模型自己填")

    p = sub.add_parser("export")
    p.add_argument("--out", required=True)
    p.add_argument("--private-words", action="append", default=[], help="私有词表文件（每行一个，只放本机）")
    p.add_argument("--allow-words", action="append", default=[], help="明知是普通词、不算命中的词表")
    p.add_argument("--include-user-entries", action="store_true")
    p.add_argument("--user-entries-license", default="")

    p = sub.add_parser("scan-tree")
    p.add_argument("path")
    p.add_argument("--private-words", action="append", default=[])
    p.add_argument("--allow-words", action="append", default=[])
    return ap


def _parse_set(items: list) -> dict:
    fields = {}
    for it in items:
        k, _, v = it.partition("=")
        k = k.strip()
        if k in ("aliases", "examples", "refs"):
            fields[k] = [x.strip() for x in v.split(",") if x.strip()]
        elif k in ("hidden", "passive"):
            fields[k] = v.strip().lower() in ("1", "true", "yes")
        elif k == "heat":
            fields[k] = float(v) if v.strip() else None
        else:
            fields[k] = v
    return fields


def _run_shelf(lib, a):
    shelf = lib.shelf()
    c = a.shelf_cmd
    if c == "add":
        return shelf.add(a.path, title=a.title, author=a.author, rights=a.rights, by=a.by, source_url=a.url)
    if c == "list":
        return shelf.list()
    if c == "read":
        return shelf.read(a.book, a.reader, chars=a.chars, restart=a.restart)
    if c == "status":
        return shelf.set_status(a.book, a.reader, a.status)
    if c == "bookmark":
        return shelf.bookmark(a.book, a.reader, note=a.note)
    if c == "bookmarks":
        return shelf.bookmarks(a.book, a.reader)
    return shelf.reading_log(a.days)


def _run_import(book, a):
    mod = __import__(f"dlolib.importers.{a.dataset}", fromlist=["run"])
    return mod.run(book, a.path or None)


def _cmd_init(a, home):
    lib = Library.init_home(home, owner_name=a.owner_name)
    res = {"ok": True, "home": str(lib.home), "imported": {}}
    for ds in a.seed:
        res["imported"][ds] = _run_import(lib.book(a.book), argparse.Namespace(dataset=ds, path=""))
    return _emit(a, res)


def _cmd_fetch_wikipedia(a, home):
    from .importers import wikipedia
    return _emit(a, wikipedia.fetch(Path(a.out) if a.out else None, delay=a.delay))


def _cmd_build_wikipedia_seed(a, home):
    from .importers import wikipedia
    return _emit(a, wikipedia.build_files())


def _cmd_scan_tree(a, home):
    from . import export
    hits = export.scan_tree(a.path, export.load_words(a.private_words), export.load_words(a.allow_words))
    print(f"扫描完成：{len(hits)} 处命中" + ("" if not hits else "（只显示位置，不回显原文）"))
    for h in hits[:100]:
        print(" ", h)
    return 1 if hits else 0


def _cmd_serve(a, lib):
    from . import server
    return server.serve(lib, a.host, a.port)


def _cmd_mcp(a, lib):
    from . import mcp_server
    return mcp_server.run(lib, a.agent)


def _cmd_export(a, lib):
    from . import export
    res = export.export(lib, a.out, private_words=export.load_words(a.private_words),
                        allow_words=export.load_words(a.allow_words),
                        include_user_entries=a.include_user_entries, user_license=a.user_entries_license,
                        book_id=a.book)
    return _emit(a, res, ok=res.get("ok"))


def _cmd_shelf(a, lib):
    return _emit(a, _run_shelf(lib, a))


def _cmd_find(a, lib, book):
    res = book.search(a.query, k=a.k, semantic=a.semantic)
    return _emit(a, res, text=render.render_search(lib, a.query, res))


def _cmd_scan(a, lib, book):
    hits = book.scan(a.message)
    return _emit(a, hits, text=render.render_scan(hits))


def _cmd_show(a, lib, book):
    e = book.get(a.id)
    return _emit(a, e or {"ok": False, "error": f"没有这一条：{a.id}"}, text=render.render_entry(lib, e) if e else None,
                 ok=bool(e))


def _cmd_list(a, lib, book):
    res = book.list(source=a.source, by=a.by, status=a.status, q=a.q, offset=a.offset, limit=a.limit)
    rows = (f"{e['id']}  {e['name']}  [{lib.label(e['source']['kind'])}]  {render.clip(e.get('brief'), 30)}"
            for e in res["items"])
    return _emit(a, res, text=f"共 {res['total']} 条\n" + "\n".join(rows))


def _cmd_stats(a, lib, book):
    return _emit(a, book.stats())


def _cmd_add(a, lib, book):
    src = {"kind": a.kind, "by": a.by or ("owner" if a.kind == "owner" else ""), "where": a.where,
           "url": a.url, "note": a.note}
    res = book.add(name=a.name, brief=a.brief, aliases=a.alias, origin=a.origin, examples=a.example, source=src,
                   refs=[{"url": a.url}] if a.url else [])
    return _emit(a, res, ok=res["ok"])


def _cmd_update(a, lib, book):
    res = book.update(a.id, _parse_set(a.set), a.by)
    return _emit(a, res, ok=res["ok"])


def _cmd_mark(a, lib, book):
    hidden = None if a.hidden is None else a.hidden == "1"
    res = book.mark(a.id, by=a.by, status=a.status, hidden=hidden)
    return _emit(a, res, ok=res["ok"])


def _cmd_remove(a, lib, book):
    res = book.remove(a.id, a.by)
    return _emit(a, res, ok=res["ok"])


def _cmd_import(a, lib, book):
    res = _run_import(book, a)
    if isinstance(res.get("skipped"), list):
        res["skipped"] = f"{len(res['skipped'])} 条（名字撞上别的条目）"
    return _emit(a, res)


NO_HOME_CMDS = {"init": _cmd_init, "fetch-wikipedia": _cmd_fetch_wikipedia,
                "build-wikipedia-seed": _cmd_build_wikipedia_seed, "scan-tree": _cmd_scan_tree}
LIB_CMDS = {"serve": _cmd_serve, "mcp": _cmd_mcp, "export": _cmd_export, "shelf": _cmd_shelf}
BOOK_CMDS = {"find": _cmd_find, "scan": _cmd_scan, "show": _cmd_show, "list": _cmd_list, "stats": _cmd_stats,
             "add": _cmd_add, "update": _cmd_update, "mark": _cmd_mark, "remove": _cmd_remove,
             "import": _cmd_import}


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    a = build_parser().parse_args(argv)
    home = Path(a.home) if a.home else default_home()
    if a.cmd in NO_HOME_CMDS:
        return NO_HOME_CMDS[a.cmd](a, home)
    lib = Library(home)
    if a.cmd in LIB_CMDS:
        return LIB_CMDS[a.cmd](a, lib)
    if a.cmd in BOOK_CMDS:
        return BOOK_CMDS[a.cmd](a, lib, lib.book(a.book))
    return 2


def _emit(a, obj, text=None, ok=True) -> int:
    if a.json or text is None:
        out_json(obj)
    else:
        print(text)
    return 0 if ok is not False else 1


if __name__ == "__main__":
    sys.exit(main())
