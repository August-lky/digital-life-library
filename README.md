# Digital Life's Own Library

**数字生命自己的图书馆**

一座放在本机的小图书馆，给 AI（以及人）当「外置的知识」：碰到没听懂的网络梗，进去查一下；弄懂了，自己存一句，下次就有了。第一本书是「中文互联网热梗」。除了工具书，还有一个普通书架，可以导入公版书或自己有的电子书，从头读到尾、记进度、夹书签。

- 只用 Python 标准库就能跑（3.10+），数据是人能直接看的 JSONL 文件，不需要数据库。
- 不认识任何具体的聊天软件或 AI 框架。「谁存的」只是一个字符串，登记在你的配置文件里。
- 可以当命令行用、当 Python 库用、当 MCP 服务接给任何会接 MCP 的 AI，也自带一个本机的管理页。

> English, in one paragraph: a local, dependency-free (stdlib only) reference library for AI companions. Look up a meme or slang term (even half a sentence, or a mis-typed variant); if it is missing, the AI can save its own one-line explanation with provenance. Ships with open-licensed seed data (CHIME, MIT; a Wikipedia list, CC BY-SA 4.0; annual buzzword lists, CC0), a CLI, a hand-written stdio MCP server, a localhost HTTP API with a small admin page, and a plain-text bookshelf (public-domain / user-owned books only).

## 快速开始

```bash
git clone https://github.com/August-lky/digital-life-library
cd digital-life-library

# 建一个图书馆目录，并导入随项目带的打底数据（不联网）
python -m dlolib --home ./my-library init --seed chime --seed wikipedia --seed yearly

# 查（半句话、写错一两个字都行）
python -m dlolib --home ./my-library find "泰酷辣"
python -m dlolib --home ./my-library find "这家店也太city了吧"

# 查不到的时候：自己去搜，弄懂了存一句
python -m dlolib --home ./my-library find "蓝色大肥鱼"
python -m dlolib --home ./my-library add --name 蓝色大肥鱼 --brief "DeepSeek 鲸鱼形象的外号" --kind owner
```

`--home` 也可以用环境变量 `DLOLIB_HOME` 指定；都不写就用 `~/.digital-life-library`。加 `--json`（写在命令前面）输出原始 JSON。

在 Python 里用：

```python
from dlolib import Library
book = Library("./my-library").book()
for hit in book.search("尊嘟假嘟"):
    print(hit["score"], hit["why"], hit["entry"]["name"], hit["entry"]["brief"])
```

## 工具书

一本书 = `<home>/books/<id>/entries.jsonl`，一行一条。书架的登记在 `<home>/books.json`，想加新书就加一行、建一个目录。

条目字段：`id, name, aliases[], brief（≤80 字，用自己的话写的短释义）, meaning, origin, examples[≤3], type, era, status(active/fading/dated/unknown), heat, hidden, flags{profanity, offense}, passive, refs[], source{...}, createdAt, updatedAt, history[≤20]`。接龙梗和歌词梗另有 `prev`（上句）/ `next`（下句），歌词梗还有 `lyric`{id, song, artist, year}（指向歌词白名单里的那首歌）。

**每条都写明从哪来**，`source.kind` 有四类：

| kind | 含义 | 必填 |
|---|---|---|
| `seed` | 打底（开放数据集、榜单导入） | `dataset`、`license`（`by` 自动是 `import`） |
| `asked` | 主人让搜的 | `by`（谁存的）+ `url` 或 `note` |
| `self` | AI 自己学的 | `by` + `where`（在哪碰到的） |
| `owner` | 主人手填 | `by` 固定是 `owner` |

每次改动都记在 `history` 里（谁、改了哪些字段、改之前是什么）。

### 检索

1. 先归一：全角转半角、转小写、去标点、连续三个以上相同的字收成两个；
2. 名字 / 别名：完全一样 → 一句话里含着梗名 → 梗名里含着这半句 → 字二元组重合 → 编辑距离（只换了一个字的变体）；
3. 四个字以上的半句，还会到出处和例句里找；
4. **读音（可选，装了 pypinyin 才有）**：认谐音写法，见下；
5. 语义（可选，fastembed）：按意思找，分数故意压低，只当「可能是」。

**被动提示** `scan()`：一句话里出现了书里的梗名就挑出来。只认三个字以上或带字母的梗名，过时、隐藏、冒犯的不提示，每条消息最多两个。**整座图书馆默认关**，要不要开由接入方决定。

冒犯 / 带脏字的条目**只打标记，不隐藏**：查到时会提醒「看场合」。

### 歌词：不收，白名单里的歌词梗例外

仓库不收歌词（版权）。但有的歌词**这句话本身成了梗**，比如杨丞琳《雨爱》的「听雨的声音 → 一滴滴清晰」（因为咒术回战新宿决战的剪辑火起来，看到上句大家会接下句），不收就查不到。所以有一份很窄的白名单 `seeds/lyric_memes_whitelist.json`：

- 每首歌一条，记：歌名、演唱者、年份、成梗的上句 `prev` / 下句 `next`、为什么成梗 `why`、和哪类剪辑 / 事件绑在一起 `boundTo`、走红时间 `popularSince`、依据链接 `sources`；
- **最多两句（上句 + 下句），每句不超过 20 个字，不收第三句、不收整段**；可以再登记至多 3 个记混的变体（如「一点点清晰」），也不超过 20 字，且和原句只差一两个字；
- 条目 `type` 是「歌词梗」（或名字正好是白名单里的句子）时，`name` / `aliases` / `prev` / `next` 只能是白名单里这首歌的句子，别的歌词句子一律拒收；
- 校验在写入 / 导入这一层（`dlolib/lyrics.py`，由 `store.py` 的 `add` / `update` / `import_entries` 调用），命令行、MCP、HTTP、导入脚本都绕不过，数字限制写在代码里；
- 其他条目的出处 / 例句 / 详细解释里，也不能抄大段歌词（引一句、20 字以内没关系）：`library_save` 存的时候抄了大段歌词，那一部分不存，其余照存，并给一句提示；名字 / 意思里就是大段歌词的，整条不存。
- 想登记新歌：先确认是「这句话本身成了梗」，写清依据链接，往白名单里加一条（加完跑测试）。不要从歌词网站批量抓。

### 可选：谐音匹配

```bash
pip install "digital-life-library[pinyin]"     # 或 pip install pypinyin
```

装了以后，`真的假的` 能找到 `尊嘟假嘟`，`太酷啦` 能认出 `泰裤辣`，`一坨大便` 能找到 `依托答辩`。只认三个音节以上的梗名，且每个音节都得对得上（允许平翘舌、前后鼻音这类差一点点）。没装、或 import 不到，这一路自动跳过，其余检索不受影响。

### 查不到时怎么长大

查不到 → 去搜 → 用 `library_save`（MCP）、`add`（命令行）或 Python 的 `book.add(...)` 存一句。过时的梗 `mark status=dated`，被动提示会跳过它；隐藏用 `hidden`。写入时会拦截手机号、身份证号、邮箱、「QQ / 群号 / 微信 + 号码」。

## 书架

```bash
python -m dlolib --home ./my-library shelf add ./某本公版书.txt --title 某本书 --rights public-domain
python -m dlolib --home ./my-library shelf read 某本书 --reader owner        # 接着上次读到的地方往下读一段
python -m dlolib --home ./my-library shelf bookmark 某本书 --note "喜欢这里"
python -m dlolib --home ./my-library shelf list
```

- **不附带任何有版权的书**，核心也从不联网下载书。只能导入 `public-domain`（公版）、`user-owned`（你自己有的）、`open-license`（明确开放许可）三种，导入时必须写明。
- 支持 `.txt`（UTF-8 / GB18030）、`.md`、`.epub`、`.html`。
- 「读者」是任意字符串；几个人一起读就用同一个读者名，进度共用。可以在 `library.config.json` 里设每人每天最多读多少字（`shelf.dailyChars`）。
- 导入时可以带上来源网址（`--url`）、许可说明（`--license-note`）和分类（`--category`）。
- **书架管理**：`shelf edit`（书名 / 作者 / 来源 / 许可说明 / 分类，书的 id 不变，进度书签跟着）、`shelf move`（换分类、挪位置：名次或 top / bottom）、`shelf remove`（拿下书架）、`shelf restore`、`shelf trash`。拿下书架**不是真删**：记录（连同进度、书签）进 `shelf/trash.json`，正文挪进 `shelf/trash/`，`restore` 原样放回；回收站里的阅读字数仍算在当天的每日额度里。核心只提供这些动作，**谁有权限用，由接入程序决定**（MCP 里没有这些工具）。
- 核心不联网取书；想从网上取公版书，由接入程序自己取回文本、写成临时文件，再用 `shelf add` 导入。

## 逛一逛

不查具体哪个梗，随便翻：

```bash
python -m dlolib --home ./my-library browse --mode topic                       # 先看有哪些话题（类型）
python -m dlolib --home ./my-library browse --mode topic --value 谐音 --page 2
python -m dlolib --home ./my-library browse --mode source --value 自己学的      # 来源：打底的数据集名 / 自己学的 / 某个 agent 的名字…
python -m dlolib --home ./my-library browse --mode recent                      # 最近新增
python -m dlolib --home ./my-library browse --mode random                      # 随便翻翻
```

每页最多 10 条（默认 8），只给名字、类型和半句意思；隐藏的条目不出现。MCP 里对应 `library_browse`。

## MCP 接入

手写的 stdio MCP 服务，不依赖 mcp SDK：

```json
{
  "mcpServers": {
    "library": {
      "command": "python",
      "args": ["-X", "utf8", "-m", "dlolib", "--home", "/path/to/my-library", "mcp", "--agent", "fish"]
    }
  }
}
```

工具：`library_lookup`、`library_save`、`library_mark`、`library_scan`、`library_browse`、`shelf_list`、`shelf_read`、`shelf_status`、`shelf_bookmark`。

**「谁存的」由 `--agent` 在启动时固定**，模型自己没法填、也没法冒名别人。agent id 要先登记在 `library.config.json` 的 `agents` 里：

```json
{ "ownerName": "主人", "agents": { "fish": { "name": "小鲸鱼" }, "sister": { "name": "姐姐" } }, "shelf": { "dailyChars": null } }
```

## 管理页和 HTTP 接口

```bash
python -m dlolib --home ./my-library serve          # http://127.0.0.1:3212/
```

- 只听本机；第一次启动会在图书馆目录里生成 `server.token`，打开页面后把令牌粘贴进去。
- 页面里可以搜、按来源 / 谁存的 / 状态筛、看详情和改动记录、手动新增、改、标过时、隐藏。通过页面写入的条目，来源一律记成「主人手填」。
- 所有会改书的请求都要带 `confirm: true`；只认 Host 是本机的请求；请求体最大 64KB。
- 接口：`GET /api/library/{books,list,entry,stats,search}`、`GET /api/shelf/list`、`POST /api/library/action {action: add|update|mark|remove, confirm: true, ...}`。

## 导入数据

| 命令 | 数据 | 许可 |
|---|---|---|
| `import chime` | CHIME 全部 1458 条（同名的一条合并，得 1457 条） | MIT |
| `import wikipedia` | 维基「中国大陆网络用语列表」的 341 个词条（词条名 + 自己写的释义） | CC BY-SA 4.0 |
| `import yearly` | 咬文嚼字 / 国家语言资源监测与研究中心 / B站年度弹幕 的上榜词（词条名 + 年份 + 自己写的释义） | CC0-1.0 |

详见 [DATA_LICENSES.md](DATA_LICENSES.md)。重复导入是安全的：没被人改过的会换成新的，被人改过的保留。

`fetch-wikipedia` 会联网读维基页面（官方 MediaWiki API，**2 个请求、间隔几秒、带清楚的 User-Agent、只读**），读不到就如实报告，不硬爬。

## 导出（做公开版之前）

```bash
python -m dlolib --home ./my-library export --out ./public --private-words ./my-private-words.txt
python -m dlolib scan-tree ./digital-life-library --private-words ./my-private-words.txt
```

- 只带许可白名单里的打底数据，**不同许可分文件放**（`entries.mit.jsonl` / `entries.cc-by-sa-4.0.jsonl` / `entries.cc0.jsonl`）。
- 剥掉谁存的、在哪碰到的、备注、改动记录、被隐藏的条目。AI 存的和主人手填的条目默认不带；要带就加 `--include-user-entries --user-entries-license CC0-1.0`。
- 用「私有词表」（群名、人名、号码，一行一个，**只放本机、别进仓库**）加上通用规则（手机号、邮箱、内网地址、本机路径、密钥格式）扫一遍；有一处命中就整个拒绝导出，报告只给位置，不回显命中的原文。

## 目录

```
dlolib/            核心（store, lyrics, shelf, render, phonetic, cli, server, mcp_server, export, importers/, web/admin.html）
seeds/chime/       CHIME 原文件 + LICENSE（MIT）
seeds/wikipedia/   词条名 + 自己写的释义（CC BY-SA 4.0）
seeds/yearly/      年度榜单（CC0-1.0）
seeds/lyric_memes_whitelist.json   歌词梗白名单（见上面「歌词」一节）
tests/             python -X utf8 -m unittest discover -s tests
DATA_LICENSES.md   每份数据的来源、许可、署名、改动
```

## 隐私

- 图书馆目录（`--home`）是你自己的数据，不在这个仓库里，也不要提交。
- 写入时拦截个人信息；导出时用私有词表扫描；管理页只听本机并要令牌。
- 核心不联网（除了你明确运行的 `fetch-wikipedia`）。

## 贡献

欢迎提 issue 和 PR。加新书的数据请先确认许可，并写进 `DATA_LICENSES.md`；释义请用自己的话写，不要照抄原文。

## 许可

代码：MIT（见 [LICENSE](LICENSE)）。数据：各自的许可，见 [DATA_LICENSES.md](DATA_LICENSES.md)。
