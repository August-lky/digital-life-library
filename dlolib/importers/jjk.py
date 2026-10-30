"""
咒术回战（含新宿决战篇）相关的中文网络梗：台词梗、接龙（上句 → 下句）、名场面、外号、二创现象。

seeds/jjk/jjk_memes.json。和 2026 盘点同一个格式、同一套导入逻辑（复用 y2026.run_seed），区别只有：
  · dataset 叫「咒术回战梗」，不会和 2026 盘点的互相冲掉；
  · 梗不限年份（2023～2026 都有），每条可以带自己的 year，ref 和 source 里就记那一年；
  · 释义和出处说明全部是本项目自己用自己的话写的，没有摘抄漫画 / 动画台词或百科原文，按 CC0-1.0 发布。
    台词只在梗名本身需要时引用极短的一句；歌词只留作为梗名的那一两个短语，不收整段。
接龙类用 type「接龙梗」，加 prev（上句）、next（下句）。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import SEEDS_DIR
from .y2026 import run_seed

DATASET = "咒术回战梗"
LICENSE = "CC0-1.0"
REF_KIND = "jjk"
DEFAULT_YEAR = 2024


def load(path=None) -> dict:
    p = Path(path) if path else SEEDS_DIR / "jjk" / "jjk_memes.json"
    return json.loads(p.read_text(encoding="utf-8"))


def run(book, path=None) -> dict:
    return run_seed(book, load(path), dataset=DATASET, license=LICENSE, ref_kind=REF_KIND,
                    default_year=DEFAULT_YEAR)
