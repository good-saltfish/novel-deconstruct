"""字数带宽与自愈路径的回归测试。

这三处判据都有过被「改回去」的历史，所以钉在测试里：

1. **字数带宽**：prompt 一度说 ±10%（2340-2860），而闸门按 0.85/1.15 判
   2210-2989。模型被要求写一个比它被检查的区间更窄的带 —— prompt 在骗模型。
2. **存量缺摘要**：机械兜底只在生成当时生效，已经写好的章永远补不上缺口，
   而 ``previous_summary`` 的唯一来源就是 ``summaries/chNNN.json``。
3. **死 run 记录**：引擎被外部杀掉后，那条 ``in_progress`` 既不会完成也不会
   重跑，台账却永远显示「一章在跑」。
"""

from __future__ import annotations

from ndecon.creation.runlog import RunLog
from ndecon.creation.summarize import backfill_summaries, missing_summary_orders
from ndecon.quality.gates import CHAPTER_TARGET_CHARS_DEFAULT, word_band

# ---- 1. 字数带宽 ----


def test_word_band_matches_gate_ratios() -> None:
    """带宽必须由闸门的 0.85/1.15 算出来，不能另写一套。"""
    low, high = word_band(2600)
    assert (low, high) == (2210, 2989)


def test_word_band_scales_with_target() -> None:
    """目标变了带宽跟着变——写死 2340/2860 就只对 2600 成立。"""
    low, high = word_band(1000)
    assert low == 850
    assert high == 1150


def test_word_band_default_target() -> None:
    """默认目标字数同样走同一条算式。"""
    low, high = word_band(CHAPTER_TARGET_CHARS_DEFAULT)
    assert low < CHAPTER_TARGET_CHARS_DEFAULT < high


def test_prompt_and_gate_use_same_band() -> None:
    """prompt 里写的区间必须与闸门真正判的区间逐字一致。"""
    from ndecon.creation.chapters import context_brief
    from ndecon.creation.models import ChapterOutline
    from ndecon.retrieval.context import ContextPack

    pack = ContextPack(
        project_id="p",
        chapter_order=1,
        chapter_outline=ChapterOutline(order=1, title="一"),
        previous_summary_source="none",
        index_doc_count=0,
        target_chars=2600,
    )
    brief = context_brief(pack)
    low, high = word_band(2600)
    assert f"{low}-{high}" in brief
    # 旧的 ±10% 数字不许再出现在 prompt 里
    assert "2340" not in brief
    assert "2860" not in brief


# ---- 2. 存量缺摘要 ----


class _StubStore:
    """只实现回填用到的那几个方法，够判据用就行。"""

    def __init__(self, manuscripts: dict[int, str], summaries: dict[int, dict]) -> None:
        self._manuscripts = manuscripts
        self._summaries = summaries
        self.written: list[tuple[int, dict]] = []

    def list_summaries(self, project_id: str) -> list[dict]:
        return [payload for payload in self._summaries.values()]

    def read_manuscript(self, project_id: str, order: int) -> str:
        return self._manuscripts.get(order, "")

    def write_summary(self, project_id: str, order: int, payload: dict) -> None:
        self._summaries[order] = payload
        self.written.append((order, payload))


class _StubMeta:
    id = "p"


class _StubProject:
    def __init__(self, cursor: int = 5) -> None:
        self.cursor = cursor
        self.meta = _StubMeta()
        self.draft = None


def test_missing_summary_orders_finds_gaps() -> None:
    """正文在、摘要不在的章才算缺口——没有正文的章不该报。"""
    store = _StubStore(
        manuscripts={1: "第一章正文", 2: "", 3: "第三章正文", 4: "第四章正文"},
        summaries={1: {"order": 1}},
    )
    assert missing_summary_orders(store, "p", cursor=4) == [3, 4]


def test_missing_summary_orders_empty_when_all_present() -> None:
    """全部齐了就不要报缺口。"""
    store = _StubStore(
        manuscripts={1: "一", 2: "二"},
        summaries={1: {"order": 1}, 2: {"order": 2}},
    )
    assert missing_summary_orders(store, "p", cursor=2) == []


def test_backfill_never_rewrites_existing() -> None:
    """已有摘要绝不覆盖：它可能带 plot_points 与证据引用，机械摘要覆盖是降级。"""
    existing = {"order": 1, "gist": "原有摘要", "plot_points": [{"id": 1}]}
    store = _StubStore(
        manuscripts={1: "一", 2: "二", 3: "三"},
        summaries={1: existing},
    )
    results = backfill_summaries(store, _StubProject())
    orders = [item["order"] for item in results]
    assert orders == [2, 3]
    assert store.written and all(order != 1 for order, _ in store.written)
    assert store.list_summaries("p")[0]["gist"] == "原有摘要"


def test_backfill_dry_run_writes_nothing() -> None:
    """dry_run 只报告不落盘——真实工作区先看清楚再决定。"""
    store = _StubStore(manuscripts={1: "一", 2: "二"}, summaries={})
    results = backfill_summaries(store, _StubProject(), dry_run=True)
    assert [item["order"] for item in results] == [1, 2]
    assert store.written == []


def test_backfill_without_writer_uses_mechanical() -> None:
    """没有 writer 时必须走机械摘要，而不是报错或跳过。"""
    store = _StubStore(manuscripts={1: "一"}, summaries={})
    results = backfill_summaries(store, _StubProject())
    assert results[0]["source"] == "mechanical"
    assert store.written[0][1]["model_id"] == "rule:mechanical-summary"


# ---- 3. 死 run 记录 ----


def _log(tmp_path, records) -> RunLog:
    log = RunLog(tmp_path, "run-x")
    for record in records:
        log.write(record)
    return log


def _record(**overrides):
    from ndecon.creation.runlog import ChapterRun

    payload = {
        "run_id": "run-x",
        "project_id": "p",
        "order": 1,
        "status": "in_progress",
        "started_at": "2026-11-04T22:51:57+08:00",
    }
    payload.update(overrides)
    return ChapterRun(**payload)


def test_reclaim_stale_settles_in_progress(tmp_path) -> None:
    """进程不在了，in_progress 必须结算成 failed。"""
    log = _log(tmp_path, [_record(order=1)])
    reclaimed = log.reclaim_stale(reason="引擎重启")
    assert reclaimed == [1]
    record = log.list_records()[0]
    assert record.status == "failed"
    assert record.error == "引擎重启"
    assert record.ended_at


def test_reclaim_stale_leaves_settled_alone(tmp_path) -> None:
    """已经结算的记录一个字都不许动。"""
    log = _log(tmp_path, [_record(order=1, status="committed")])
    assert log.reclaim_stale(reason="引擎重启") == []
    assert log.list_records()[0].status == "committed"


def test_reclaim_stale_skips_current_run(tmp_path) -> None:
    """内存里正在跑的那一条别碰，否则会把活的写成失败。"""
    log = _log(tmp_path, [_record(order=1)])
    assert log.reclaim_stale(reason="x", current_run_id="run-x") == []
    assert log.list_records()[0].status == "in_progress"


def test_reclaim_stale_is_idempotent(tmp_path) -> None:
    """跑第二次不该再回收——否则每次打开台账都在重写文件。"""
    log = _log(tmp_path, [_record(order=1)])
    assert log.reclaim_stale(reason="引擎重启") == [1]
    assert log.reclaim_stale(reason="引擎重启") == []
