"""Run 记录：每章一次生成尝试都留一条可重放的账。

为什么必须有它（B4）：

全自动连写 300 章，出问题时人的第一个问题是"这一章是什么模型、什么上下文、
第几次尝试、过没过门、花了多少钱"。没有 run record，这些问题只能靠猜；
有了它，一章可以从记录里重放，成本可以累加，异常可以按 provider 归因。

落点：``projects/<id>/runs/<run_id>/ch<order>.json``，一次连跑一个 run_id，
每章一个文件——不写成一个大 JSON，避免 300 章时单文件写坏就全丢。

纪律：
- 先写 ``status="in_progress"``，跑完再改成终态；进程被杀时这条记录就是断点；
- 只有终态记录才可信；in_progress 记录由 resume 逻辑回收（重跑该章）；
- 记录里存哈希与统计，**不存正文**（正文在 manuscripts/chNNN.md）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ndecon.workspace.atomic import atomic_write_json, read_text_with_retry

RunStatus = Literal["in_progress", "committed", "rolled_back", "failed"]
GateVerdict = Literal["pass", "repair", "reject"]


class ChapterRun(BaseModel):
    """单章一次生成尝试的账。"""

    run_id: str = Field(min_length=1, description="一次连跑的标识（含时间戳）")
    project_id: str = Field(min_length=1)
    order: int = Field(ge=1)
    provider: str = Field(default="", description="provider 名（fake / ai / 具体模型）")
    model: str = Field(default="", description="实际使用的模型 id")
    prompt_version: str = Field(default="")
    context_hash: str = Field(default="", description="上下文包哈希，用于判断'同样输入'")
    output_hash: str = Field(default="", description="正文 SHA-256 前缀")
    gate_verdict: GateVerdict = Field(default="pass")
    gate_failures: list[str] = Field(default_factory=list, description="硬门失败规则标识")
    gate_details: list[str] = Field(
        default_factory=list,
        description="硬门失败的可读说明（含实测值），与 gate_failures 同序",
    )
    soft_scores: dict[str, float] = Field(default_factory=dict)
    attempts: int = Field(default=1, ge=1, description="本章累计尝试次数")
    # 实测汉字数：必须落盘。面板台账读的就是这个字段，缺了它「每章多少字」
    # 永远显示空白 —— 而字数是最直观的质量信号，用户据此判断该不该换模型。
    cjk_chars: int = Field(default=0, ge=0, description="本章正文的实测汉字数")
    summary_present: bool = Field(default=False, description="是否产出结构化摘要")
    summary_source: str = Field(
        default="",
        description="model=模型读过；mechanical=机械兜底（不调模型，只保证不断档）",
    )
    cost_tokens: int = Field(default=0, ge=0, description="本次消耗 token 估算")
    status: RunStatus = Field(default="in_progress")
    started_at: str = Field(default="")
    ended_at: str = Field(default="")
    error: str = Field(default="", description="失败原因（成功时为空）")

    @property
    def settled(self) -> bool:
        """终态记录才算数；in_progress 是断点标记。"""
        return self.status != "in_progress"


def now_iso() -> str:
    """本地时间戳，精确到秒（记录排序与人工阅读都够用）。"""
    return datetime.now().isoformat(timespec="seconds")


def new_run_id(prefix: str = "run") -> str:
    """生成一次连跑的 run_id：前缀 + 秒级时间戳，人工可读且天然有序。"""
    return f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


class RunLog:
    """一次连跑的记录目录（projects/<id>/runs/<run_id>/）。"""

    def __init__(self, project_dir: Path, run_id: str) -> None:
        """绑定到项目目录与 run_id；目录在第一次写入时创建。"""
        if not run_id or "/" in run_id or "\\" in run_id:
            raise ValueError(f"非法 run_id：{run_id!r}")
        self.project_dir = Path(project_dir)
        self.run_id = run_id

    @property
    def dir(self) -> Path:
        """本次连跑的记录目录。"""
        return self.project_dir / "runs" / self.run_id

    def _path(self, order: int) -> Path:
        return self.dir / f"ch{order:03d}.json"

    def read(self, order: int) -> ChapterRun | None:
        """读取某章记录；不存在或损坏返回 None（损坏不拖垮整次连跑）。"""
        path = self._path(order)
        if not path.is_file():
            return None
        try:
            return ChapterRun.model_validate(json.loads(read_text_with_retry(path)))
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def write(self, record: ChapterRun) -> Path:
        """原子写一条记录（tmp + os.replace），中途崩溃不会留半截 JSON。"""
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._path(record.order)
        atomic_write_json(path, record.model_dump())
        return path

    def list_records(self) -> list[ChapterRun]:
        """按章号列出本次连跑的全部记录（跳过损坏文件）。"""
        if not self.dir.is_dir():
            return []
        records: list[ChapterRun] = []
        for path in sorted(self.dir.glob("ch*.json")):
            try:
                records.append(
                    ChapterRun.model_validate(json.loads(read_text_with_retry(path)))
                )
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return records

    def committed_orders(self) -> set[int]:
        """已成功提交的章号集合——resume 的判据是"这章已 committed"。"""
        return {rec.order for rec in self.list_records() if rec.status == "committed"}

    def unsettled_orders(self) -> set[int]:
        """上次被杀时停在 in_progress 的章号，resume 时必须重跑。"""
        return {rec.order for rec in self.list_records() if not rec.settled}

    def total_cost_tokens(self) -> int:
        """本次连跑累计 token——预算硬停的输入。"""
        return sum(rec.cost_tokens for rec in self.list_records())

    def reclaim_stale(self, *, reason: str, current_run_id: str | None = None) -> list[int]:
        """把已中断的 ``in_progress`` 记录结算成 ``failed``，返回被回收的章号。

        为什么必须做这件事（实测现场）：用户项目里有两份 ``ch13.json``
        永远停在 ``in_progress``、``verdict=pass``、``cjk_chars=0``——那一次引擎
        被外部杀掉，章事务没跑完。这条记录**不会自己消失**：

        - ``settled`` 永远 False，面板台账就一直显示「一章在跑」；
        - 它又不是可重跑的断点（正文根本没落盘，游标也没动）；
        - 于是每次打开台账，用户看到的都是同一件陈年事故。

        判据是机械的：**进程已经不在跑了**（``current_run_id`` 是内存里真正在跑
        的那一个，不是它就是死记录），且状态还是 ``in_progress``。这不是猜，
        是「谁在写这条记录」的唯一真相——内存任务表。

        回收只改记录状态，**绝不重写正文、绝不动游标**：那章的正文要么根本没写，
        要么已被下一次重跑覆盖，都不该由这里碰。
        """
        reclaimed: list[int] = []
        for record in self.list_records():
            if record.settled:
                continue
            if current_run_id is not None and record.run_id == current_run_id:
                continue  # 这一章正在被活着的线程写，别碰
            record.status = "failed"
            record.ended_at = now_iso()
            record.error = reason
            self.write(record)
            reclaimed.append(record.order)
        return reclaimed
