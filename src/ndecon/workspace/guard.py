"""验证脚本的工作区守卫：只允许在系统临时目录下做写操作验证。

为什么需要它（真实事故 2026-11-04）：

    为了验证「字数目标已写进 prompt」，跑了

        python -m ndecon.cli chapter creation-无敌大坤巴-180051 1 --provider fake

    这是一条**写命令**，它把一章 Fake 占位正文写进了用户自己的项目：
    `manuscripts/ch001.md`、`summaries/ch001.json`、一条 run 记录，`cursor` 从 0 变 1。
    虽然已经回滚（删正文、删摘要、删 run、游标复位），但这类事故的代价
    （信任、数据、时间）远高于验证本身的价值。

规则：验证脚本只要会写工作区，就先调 :func:`require_temp_workspace`。
工作区不在系统临时目录下就 `SystemExit(2)` 退出，不做任何写操作。

用法::

    import tempfile
    from pathlib import Path
    from ndecon.workspace.guard import require_temp_workspace

    ws = require_temp_workspace(Path(tempfile.mkdtemp(prefix="ndecon-verify-")))
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def _allowed_roots() -> tuple[Path, ...]:
    """系统临时目录（Windows 上 TEMP 与 TMP 通常同一个，去重后一个）。"""
    candidates = {tempfile.gettempdir(), os.environ.get("TEMP", ""), os.environ.get("TMP", "")}
    return tuple(Path(p).resolve() for p in candidates if p)


def is_temp_path(path: str | Path) -> bool:
    """判断路径是否落在系统临时目录下。"""
    resolved = Path(path).resolve()
    return any(resolved == root or root in resolved.parents for root in _allowed_roots())


def require_temp_workspace(path: str | Path) -> Path:
    """校验工作区在临时目录下；否则打印原因并 `SystemExit(2)`。

    返回解析后的路径，方便调用方接着用。
    """
    resolved = Path(path).resolve()
    if is_temp_path(resolved):
        return resolved
    roots = "、".join(str(r) for r in _allowed_roots())
    print(
        f"拒绝在非临时目录上做写操作验证：{resolved}\n"
        f"允许的根：{roots}\n"
        "理由：验证脚本曾把 Fake 占位正文写进用户真实项目（2026-11-04 事故）。\n"
        "要验证请把工作区放到临时目录下，例如：\n"
        '    ws = Path(tempfile.mkdtemp(prefix="ndecon-verify-"))',
        file=sys.stderr,
    )
    raise SystemExit(2)
