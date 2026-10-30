"""
stdio 版 MCP 服务（手写协议，不依赖 mcp SDK）。任何会接 MCP 的 AI 都能用。

  python -m dlolib --home <图书馆目录> mcp --agent <id>

--agent 在启动时固定「谁存的」：library_save / library_mark 写进去的来源一律是这个人，
模型自己没法填、也没法冒名别人。agent id 必须是 library.config.json 里登记过的（没登记任何人时随意）。

协议：每行一条 JSON-RPC 消息（换行分隔），支持 initialize / ping / tools/list / tools/call。
工具说明写给 AI 看，用家常话，写「可以」，不催人。
"""
from __future__ import annotations

import json
import sys

from . import render
from .shelf import READ_STATUS
from .store import STATUSES, Library

PROTOCOL = "2024-11-05"
TOGETHER = "together"          # 「一起读」共用的读者名：进度共用，谁读都往下走

TOOLS = [
    {
        "name": "library_lookup",
        "description": "在图书馆的工具书里查一个梗、一句话或半句话（比如别人说的一句你没听懂的网络用语）。"
                       "可以只给半句，写错一两个字也找得到。找不到的话，可以去搜一下，弄懂了再用 library_save 存一句进来。"
                       "歌词不收（只有已经成了梗的一两句例外），歌词可以另外查。",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string", "description": "想查的词或句子"},
            "k": {"type": "integer", "description": "最多给几条，默认 3"}}, "required": ["query"]},
    },
    {
        "name": "library_save",
        "description": "把你弄懂的一个梗用自己的话存进图书馆，下次就能直接查到。要写：梗的名字、一句话意思（80 字以内，用自己的话），"
                       "以及在哪碰到的（where，比如哪个群、哪个视频）；如果是自己去搜到的，可以附上网址（url）。"
                       "不要存别人的号码、手机号、邮箱这类个人信息，也不用抄整段歌词（歌词不收，抄了的那部分不会存）。已经有的梗不会重复存。",
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string"}, "brief": {"type": "string", "description": "一句话意思，80 字以内，用自己的话"},
            "where": {"type": "string", "description": "在哪碰到的"}, "url": {"type": "string", "description": "查到的网址（可选）"},
            "aliases": {"type": "array", "items": {"type": "string"}, "description": "另外的写法（可选）"},
            "origin": {"type": "string", "description": "出处或来历（可选）"}}, "required": ["name", "brief"]},
    },
    {
        "name": "library_mark",
        "description": "给图书馆里的一条标状态：过时了（dated）、有点过气（fading）、正流行（active），也可以隐藏它。"
                       "标成过时的梗不会再被主动提示，但查的时候还是找得到。",
        "inputSchema": {"type": "object", "properties": {
            "id": {"type": "string", "description": "条目 id（library_lookup 的结果里有）"},
            "status": {"type": "string", "enum": list(STATUSES)}, "hidden": {"type": "boolean"}}, "required": ["id"]},
    },
    {
        "name": "library_scan",
        "description": "看一句话里有没有图书馆里收着的梗（只认三个字以上或带字母的梗名）。用不用、怎么接，由你自己决定。",
        "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]},
    },
    {
        "name": "shelf_list",
        "description": "看书架上有哪些书、各自读到哪了。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "shelf_read",
        "description": "接着上次读到的地方往下读一段（停在段落结尾）。读到哪会自动记住。想从头读可以 restart。",
        "inputSchema": {"type": "object", "properties": {
            "book": {"type": "string", "description": "书名或书 id"},
            "chars": {"type": "integer", "description": "这一段大概多少字，默认 1500"},
            "restart": {"type": "boolean"},
            "together": {"type": "boolean", "description": "和别人一起读：几个人共用同一份进度（谁读都往下走）"}},
            "required": ["book"]},
    },
    {
        "name": "shelf_status",
        "description": "给书架上的一本书标一下你自己的状态：想读（want）、在读（reading）、读完了（done）。",
        "inputSchema": {"type": "object", "properties": {
            "book": {"type": "string", "description": "书名或书 id"},
            "status": {"type": "string", "enum": ["want", "reading", "done"]}}, "required": ["book", "status"]},
    },
    {
        "name": "shelf_bookmark",
        "description": "在现在读到的位置夹一个书签，可以写一句自己的感想。",
        "inputSchema": {"type": "object", "properties": {
            "book": {"type": "string"}, "note": {"type": "string"},
            "together": {"type": "boolean", "description": "夹在「一起读」的进度上"}}, "required": ["book"]},
    },
]


class Handler:
    def __init__(self, lib: Library, agent: str):
        agents = lib.agents
        if agents and agent not in agents:
            raise ValueError(f"不认识的 agent：{agent}（登记过的有：{'、'.join(agents)}）")
        self.lib, self.agent = lib, agent

    # ── 工具 ──
    def call(self, name: str, args: dict) -> str:
        lib, who = self.lib, self.agent

        def reader_of(a):
            return TOGETHER if a.get("together") else who
        if name == "library_lookup":
            q = str(args.get("query", ""))
            res = lib.book().search(q, k=max(1, min(10, int(args.get("k") or 3))))
            return render.render_search(lib, q, res)
        if name == "library_save":
            src = {"kind": "asked" if args.get("url") else "self", "by": who, "where": str(args.get("where", "")),
                   "url": str(args.get("url", ""))}
            res = lib.book().add(name=args.get("name"), brief=args.get("brief"), source=src,
                                 aliases=args.get("aliases"), origin=str(args.get("origin", "")),
                                 refs=[{"url": args["url"]}] if args.get("url") else [])
            if res["ok"]:
                note = f"（{res['note']}）" if res.get("note") else ""
                return f"存好了：「{res['entry']['name']}」。下次可以直接查到。{note}"
            return "没存成：" + res["error"]
        if name == "library_mark":
            res = lib.book().mark(str(args.get("id", "")), who, status=args.get("status"), hidden=args.get("hidden"))
            return "已更新。" if res["ok"] else "没改成：" + res["error"]
        if name == "library_scan":
            return render.render_scan(lib.book().scan(str(args.get("message", ""))))
        shelf = lib.shelf()
        if name == "shelf_list":
            rows = shelf.list()
            if not rows:
                return "书架上还没有书。"

            def line(b):
                mine, together = b["progress"].get(who, 0), b["progress"].get(TOGETHER, 0)
                state = READ_STATUS.get((b.get("status") or {}).get(who), "")
                extra = f"；一起读到 {together} 字" if together else ""
                return f"《{b['title']}》{b['author'] or ''}　{('（' + state + '）') if state else ''}你读到 {mine}/{b['chars']} 字{extra}"
            return "\n".join(line(b) for b in rows)
        if name == "shelf_status":
            r = shelf.set_status(str(args.get("book", "")), who, str(args.get("status", "")))
            return f"记下了：《{r['book']}》{READ_STATUS[r['status']]}。" if r["ok"] else "没改成：" + r["error"]
        if name == "shelf_read":
            r = shelf.read(str(args.get("book", "")), reader_of(args), chars=int(args.get("chars") or 1500),
                           restart=bool(args.get("restart")), charge=who)
            if not r["ok"]:
                return r["error"], {"tool": "shelf_read", "ok": False, "limitReached": bool(r.get("limitReached"))}
            tail = "（这本读完了）" if r["finished"] else f"（读到 {r['percent']}%）"
            return f"《{r['title']}》\n{r['text']}\n{tail}", {
                "tool": "shelf_read", "ok": True, "title": r["title"], "chars": len(r["text"]), "from": r["from"],
                "to": r["to"], "total": r["total"], "finished": r["finished"], "todayChars": r["todayChars"],
                "together": bool(args.get("together"))}
        if name == "shelf_bookmark":
            r = shelf.bookmark(str(args.get("book", "")), reader_of(args), note=str(args.get("note", "")))
            m = r["bookmark"]
            return f"夹好了：《{r['book']}》第 {m['pos']} 字处。", {
                "tool": "shelf_bookmark", "ok": True, "title": r["book"], "pos": m["pos"], "quote": m["quote"],
                "note": m["note"], "together": bool(args.get("together"))}
        raise KeyError(f"没有这个工具：{name}")

    # ── 协议 ──
    def handle(self, msg: dict):
        method, mid = msg.get("method"), msg.get("id")
        if method == "initialize":
            ver = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL
            return self._ok(mid, {"protocolVersion": ver, "capabilities": {"tools": {}},
                                  "serverInfo": {"name": "digital-life-library", "version": "0.1.0"}})
        if method == "ping":
            return self._ok(mid, {})
        if method == "tools/list":
            return self._ok(mid, {"tools": TOOLS})
        if method == "tools/call":
            return self._ok(mid, self._tool_result(msg.get("params") or {}))
        if mid is None:                                  # 通知（notifications/initialized 之类）不用回
            return None
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"不支持 {method}"}}

    def _tool_result(self, params: dict) -> dict:
        """调用一个工具，把结果（或「没成功」的说明）包成 MCP 的 content。出错要告诉调用方，不能让服务挂掉。"""
        structured = None
        try:
            text, is_error = self.call(params.get("name", ""), params.get("arguments") or {}), False
            if isinstance(text, tuple):                 # 书架工具：（给 AI 看的话，给接入程序用的结构化结果）
                text, structured = text
        except (KeyError, TimeoutError) as e:
            text, is_error = str(e.args[0]) if e.args else str(e), True
        except Exception as e:                          # noqa: BLE001
            text, is_error = f"没成功（{type(e).__name__}）", True
        out = {"content": [{"type": "text", "text": text}]}
        if structured is not None:
            out["structuredContent"] = structured
        if is_error:
            out["isError"] = True
        return out

    @staticmethod
    def _ok(mid, result):
        return None if mid is None else {"jsonrpc": "2.0", "id": mid, "result": result}


def run(lib: Library, agent: str, stdin=None, stdout=None) -> int:
    if stdin is None and stdout is None:
        for stream in (sys.stdin, sys.stdout):          # Windows 默认不是 UTF-8，中文会乱
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    try:
        h = Handler(lib, agent)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "读不懂"}}) + "\n")
            stdout.flush()
            continue
        reply = h.handle(msg) if isinstance(msg, dict) else None
        if reply:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0
