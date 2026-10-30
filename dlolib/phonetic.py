"""
谐音匹配的可选钩子。

装了 pypinyin（MIT）就能认「尊嘟假嘟 ~ 真的假的」「泰酷辣 ~ 太酷啦」这类读音相近的写法；
没装、或者 import 不到，这一层整个跳过，检索照常工作（只是少了谐音这一路）。

核心不强制依赖 pypinyin：
  - 想用：pip install pypinyin
  - 测试或别的拼音来源：phonetic.set_converter(函数)，函数把一段文字变成音节列表。
"""
from __future__ import annotations

# 平翘舌、前后鼻音、n/l 这几对，很多人读起来分不清（也是谐音梗最常用的），算「差一点点」。
_FUZZY_INITIALS = (("z", "zh"), ("c", "ch"), ("s", "sh"), ("n", "l"), ("f", "h"))
_FUZZY_FINALS = (("an", "ang"), ("en", "eng"), ("in", "ing"), ("un", "en"), ("on", "ong"))
# 卖萌的说法常把 e 念成 u（真的 → 尊嘟、可爱 → 可嗳），也算差一点
_CUTE_FINALS = ({"e", "u"}, {"i", "u"}, {"ei", "u"})
_INITIALS = ("zh", "ch", "sh", "b", "p", "m", "f", "d", "t", "n", "l", "g", "k", "h", "j", "q", "x",
             "r", "z", "c", "s", "y", "w")

_STATE = {"converter": None, "tried": False}


def set_converter(func):
    """手动指定拼音转换函数（传 None 就当没有）。"""
    _STATE["converter"] = func
    _STATE["tried"] = True


def reset():
    """回到「还没试过」的状态，下一次再去 import pypinyin（测试用）。"""
    _STATE["converter"] = None
    _STATE["tried"] = False


def _load():
    if not _STATE["tried"]:
        _STATE["tried"] = True
        try:
            from pypinyin import lazy_pinyin
            _STATE["converter"] = lambda text: lazy_pinyin(text, errors="default")
        except Exception:
            _STATE["converter"] = None
    return _STATE["converter"]


def available() -> bool:
    return _load() is not None


def syllables(text: str):
    """把文字变成音节列表（没有拼音库就返回 None）。"""
    conv = _load()
    if conv is None or not text:
        return None
    try:
        return [s.lower() for s in conv(text) if s]
    except Exception:
        return None


def _split(syl: str):
    for ini in _INITIALS:
        if syl.startswith(ini) and len(syl) > len(ini):
            return ini, syl[len(ini):]
    return "", syl


def syllable_cost(a: str, b: str):
    """两个音节的差别：完全一样 0，只差平翘舌 / 前后鼻音这类 0.35，卖萌式的 e/u 互换 0.5；
    差得更多就返回 None（一个字都对不上的，整个梗名就不算谐音）。"""
    if a == b:
        return 0.0
    ia, fa = _split(a)
    ib, fb = _split(b)
    ok_i = ia == ib or (ia, ib) in _FUZZY_INITIALS or (ib, ia) in _FUZZY_INITIALS
    if not ok_i:
        return None
    if fa == fb or (fa, fb) in _FUZZY_FINALS or (fb, fa) in _FUZZY_FINALS:
        return 0.35
    if {fa, fb} in _CUTE_FINALS:
        return 0.5
    return None


def align_cost(a: list, b: list):
    """两串音节逐个比（等长，只算替换），返回总差别；长度不同、或有一个音节对不上就返回 None。"""
    if len(a) != len(b):
        return None
    total = 0.0
    for x, y in zip(a, b):
        c = syllable_cost(x, y)
        if c is None:
            return None
        total += c
    return total


def best_window(query_syl: list, key_syl: list):
    """在 query 的音节里滑一个和梗名一样长的窗口，返回最小差别（话里夹着谐音写法也能找到）。"""
    n = len(key_syl)
    if n == 0 or len(query_syl) < n:
        return None
    costs = [align_cost(query_syl[i:i + n], key_syl) for i in range(len(query_syl) - n + 1)]
    costs = [c for c in costs if c is not None]
    return min(costs) if costs else None


def phonetic_score(query_syl: list, key_syl: list):
    """返回 (分数, 说明) 或 None。只认 3 个音节以上的梗名（两个字的词同音字太多，会满屏误报）。"""
    if not query_syl or len(key_syl) < 3:
        return None
    cost = best_window(query_syl, key_syl)
    if cost is None:
        return None
    n = len(key_syl)
    sim = 1 - cost / n
    whole = len(query_syl) == n
    if cost == 0:
        return (0.9 if whole else 0.8), "读音一样"
    if sim >= 0.6:
        return round((0.82 if whole else 0.72) * sim, 3), "读音相近"
    return None
