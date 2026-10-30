"""
数据导入器。每个数据集一个文件，都只读 seeds/ 里随项目带的文件（不联网）。
联网读取（维基）是单独的 fetch 命令，读下来存成 seeds/ 里的文件，导入时再从文件读。
"""
from pathlib import Path

SEEDS_DIR = Path(__file__).resolve().parents[2] / "seeds"

IMPORTERS = ("chime", "wikipedia", "yearly", "y2026", "jjk")
