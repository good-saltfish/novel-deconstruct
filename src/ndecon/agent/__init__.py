"""细纲/设定 Planner Agent（ADR-0006，#25）。

模型在窄任务内通过受限工具循环自主查阅方法论/前文/伏笔并产出节拍与设定候选；
工具层确定、可测、离线可跑（FakePlanner），候选经用户确认才入库。
"""

from __future__ import annotations
