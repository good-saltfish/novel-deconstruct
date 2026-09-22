"""受控词表：模型输出只能取这些合法值，非法值在 schema 校验阶段即被拒绝。

词表来源：拆书方法论中 Stage 2 章节摘要的既有约定（基调 10 值、主题标签 12 值），
用于保证跨章、跨书产物可聚合、可统计。
"""

from __future__ import annotations

from enum import Enum


class Tone(str, Enum):
    """单个情节点的叙事基调（10 值受控词表）。"""

    TENSE = "紧张"
    RELAXED = "轻松"
    SAD = "悲伤"
    HOT_BLOODED = "热血"
    SATISFYING = "爽"
    SWEET = "甜"
    WARM = "温馨"
    HORROR = "恐怖"
    OPPRESSIVE = "压抑"
    OTHER = "其他"


class ThemeTag(str, Enum):
    """单个情节点的主题标签（12 值受控词表）。"""

    LOVE = "爱情"
    KINSHIP = "亲情"
    FRIENDSHIP = "友情"
    POWER = "权力"
    MONEY = "金钱"
    GROWTH = "成长"
    REVENGE = "复仇"
    SUSPENSE = "悬念"
    COMEDY = "搞笑"
    HOT_BLOODED = "热血"
    DAILY = "日常"
    OTHER = "其他"
