"""写作台账脚手架（SOP 第 0 周）：人物卡 / 伏笔账本 / 章节蓝图。

只搭骨架，不代笔——三条纪律：
1. 模板是**空表 + 填写说明**，正文与设定一律由作者写；本模块不产出任何创意内容；
2. 传入创作项目时**只搬运项目里已有的结构化内容**（人设、细纲章题/钩子），
   不调模型、不联网、不补写——项目里没有的字段，表里就留空；
3. 伏笔只登记细纲里**显式写出**的章尾钩，与 `retrieval/foreshadow.py` 同一纪律：
   没有章尾钩的章不进账本，宁缺毋滥。

文件名用 ASCII（`character-card.md` 等）而非中文名：命令行在部分 Windows
终端下回显中文路径会踩编码问题，文件名保持 ASCII、中文标题写在文件内更稳。
"""

from __future__ import annotations

from pathlib import Path

from ndecon.retrieval.foreshadow import extract_foreshadows
from ndecon.workspace.models import CreationProject

SCAFFOLD_VERSION = "ledger-v1"

CHARACTER_CARD = "character-card.md"
FORESHADOW_LEDGER = "foreshadow-ledger.md"
CHAPTER_BLUEPRINT = "chapter-blueprint.md"
SCAFFOLD_FILENAMES = (CHARACTER_CARD, FORESHADOW_LEDGER, CHAPTER_BLUEPRINT)

# 台账文件头：写清"谁生成的、内容归谁"，避免误以为是 AI 代笔产物
_BANNER = "由 `ndecon scaffold` 生成（{version}）｜模板只搭骨架，**内容由你写**。"


def _head(title: str, purpose: str) -> str:
    """统一的 Markdown 文件头：标题 + 生成来源声明 + 用途说明。"""
    return f"# {title}\n\n> {_BANNER.format(version=SCAFFOLD_VERSION)}\n> {purpose}\n"


def character_card(project: CreationProject | None = None) -> str:
    """人物卡模板；有项目时用人设字段预填，没有的字段留空。"""
    protagonist = project.draft.protagonist if project is not None else None
    lines = [
        _head(
            "人物卡",
            "语音卡（SOP P3）+ 卡普曼三角配置是本卡的核心："
            "遮住名字仍能认出是谁，角色才算立住。",
        ),
        "## 主角",
        "",
        "| 字段 | 内容 |",
        "|---|---|",
        f"| 姓名 | {protagonist.name if protagonist else ''} |",
        f"| 出身/初始处境 | {protagonist.background if protagonist else ''} |",
        f"| 性格 | {protagonist.personality if protagonist else ''} |",
        f"| 核心欲望 | {protagonist.desire if protagonist else ''} |",
        f"| 缺陷/成长课题 | {protagonist.flaw if protagonist else ''} |",
        f"| 标志能力/手段 | {protagonist.signature_ability if protagonist else ''} |",
        "| 语音特征（口头禅、句式、用词禁区） |  |",
        "| 卡普曼三角常用位 | 迫害者 / 受害者 / 拯救者（圈一个，并写何时切换） |",
        "| 区分度自检（遮住名字还认得出吗） |  |",
        "",
        "## 主要配角",
        "",
        "| 姓名 | 功能（推动/阻碍/陪衬） | 欲望 | 语音特征 | 三角常用位 |",
        "|---|---|---|---|---|",
        "|  |  |  |  |  |",
        "",
        "## 填写提醒",
        "",
        "- 语音卡自检：把一段对话单独抽出来，遮住名字还认得出是谁吗？认不出 = 区分度不足。",
        "- 缺陷不要用「太善良」「太较真」这类伪缺陷（SOP：避免完美主角）。",
        "- 三角位不是固定人设：同一个人在不同场景会换位，换位本身就是冲突来源。",
        "",
    ]
    return "\n".join(lines)


def foreshadow_ledger(project: CreationProject | None = None) -> str:
    """伏笔账本模板；有项目时按细纲章尾钩预填（只登记显式写出的钩子）。"""
    rows: list[str] = []
    if project is not None and project.draft.chapters:
        for entry in extract_foreshadows(project.draft.chapters):
            evidence = entry.resolution_evidence.replace("\n", " ")[:40]
            rows.append(
                f"| {entry.key} | {entry.opened_order} | {entry.description} |  | "
                f"{entry.resolved_order or ''} | {evidence} |"
            )
    if not rows:
        rows.append("| hook-ch1 | 1 |  |  |  |  |")
    lines = [
        _head(
            "伏笔账本",
            "只登记**你在细纲里显式写出**的章尾钩；没有章尾钩的章不进表，宁缺毋滥。",
        ),
        "| 编号 | 开启章 | 钩子（章尾钩原文） | 计划回收章 | 实际回收章 | 回收证据 |",
        "|---|---|---|---|---|---|",
        *rows,
        "",
        "## 规则",
        "",
        "- 开新钩：细纲章尾钩里写清楚，写完立刻回这里登记一行。",
        "- 回收：写完回收章后填「实际回收章 + 证据」，证据要能指向具体段落。",
        "- 每章开写前：先看写到本章前**仍未回收**的债"
        "（对应 `open_foreshadows_at()` 的口径）。",
        "- 宁缺毋滥：没回收就留空，不要为了「看起来清了」硬填。",
        "",
    ]
    return "\n".join(lines)


def chapter_blueprint(project: CreationProject | None = None) -> str:
    """章节蓝图模板；有项目时按前十章细纲预填章题与钩子。"""
    rows: list[str] = []
    if project is not None and project.draft.chapters:
        for chapter in project.draft.chapters:
            rows.append(
                f"| {chapter.order} | {chapter.title} | {chapter.core_event} | "
                f"{chapter.opening_hook} | {chapter.ending_hook} |  |  |  | 未写 |"
            )
    if not rows:
        rows.append("| 1 |  |  |  |  |  |  |  | 未写 |")
    lines = [
        _head(
            "章节蓝图",
            "一章一行：三角配置与情绪走向**开写前**填，状态**写完后**更新。",
        ),
        "| 章号 | 章题 | 核心事件 | 开篇钩 | 章尾钩 | 三角配置 | 情绪走向 | 目标字数 | 状态 |",
        "|---|---|---|---|---|---|---|---|---|",
        *rows,
        "",
        "## 用法",
        "",
        "- 三角配置：迫害者 / 受害者 / 拯救者各是谁；缺一角 = 这章大概率平淡。",
        "- 情绪走向：一句写清本章读者该被推到哪个情绪点（期待 / 憋屈 / 爆发 / 后怕）。",
        "- 写完一章后回来更新状态：`未写 → 已写 → 已体检`（体检指 `ndecon lint`）。",
        "",
    ]
    return "\n".join(lines)


def build_scaffold(project: CreationProject | None = None) -> dict[str, str]:
    """生成三份台账的 {文件名: 内容} 映射（纯函数，不落盘）。"""
    return {
        CHARACTER_CARD: character_card(project),
        FORESHADOW_LEDGER: foreshadow_ledger(project),
        CHAPTER_BLUEPRINT: chapter_blueprint(project),
    }


def write_scaffold(
    out_dir: Path,
    project: CreationProject | None = None,
    force: bool = False,
) -> tuple[list[Path], list[Path]]:
    """把三份台账写到 out_dir；已存在的文件默认跳过，force 时才覆盖。

    返回 (已写入, 已跳过)——调用方据此如实汇报，绝不静默覆盖作者已有的台账。
    """
    written: list[Path] = []
    skipped: list[Path] = []
    out_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in build_scaffold(project).items():
        path = out_dir / filename
        if path.is_file() and not force:
            skipped.append(path)
            continue
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written, skipped
