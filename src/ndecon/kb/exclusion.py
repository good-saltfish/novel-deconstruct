"""资料库收录/排除判定（ADR-0005 的版权闸门）。

判定完全基于路径、大小与文本形态，结果带人类可读原因并写入 manifest 供审计。
规则顺序固定、输出确定：同一棵目录树永远得到同一份收录清单。

硬排除的 C 类形态：
- 路径中存在名为"原文"的目录；
- 采集缓存（.progress.json）；
- "书名 - 作者.txt"全本命名、含"原文"的 txt、前40章文本、单章 txt；
- 超过大小上限的文件（小说全文通常数 MB，方法论资料远小于此）；
- 章节标题启发式：md/txt 中大量出现"第N章/节/回/部"行（含 `## 第1章` markdown 形态），
  判为小说正文；自研分析内容有两重豁免（路径标记、评论术语密度）；
- v1 不支持的文件类型（doc/xlsx/zip/png 等）跳过但不算违规。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# v1 支持解析的知识文件类型
SUPPORTED_SUFFIXES = {".md", ".txt", ".docx"}
# 单文件大小上限（1MB，与 CI 仓库卫生阈值一致）
MAX_FILE_BYTES = 1024 * 1024
# 章节启发式阈值：至少出现多少个章节行才判定为小说正文
CHAPTER_LINE_THRESHOLD = 8
# 启发式只扫描文本前部，避免对大文件做无谓全量读取
CHAPTER_SCAN_BYTES = 512 * 1024
# 自研分析内容的评论术语下限：达到即视为拆解/大纲/详案（真实语料实测间隔 2 vs 11，取 8）
ANALYSIS_TERM_MIN = 8

# "第3章"/"第十二回"/"## 第345节" 等行首章节标记（允许 markdown 标题前缀；
# 2026-09 dogfood：版权全本以 `## 第1章` md 形态归档，旧规则零漏过）
_CHAPTER_LINE_RE = re.compile(r"^\s*#{0,6}\s*第[0-9零一二三四五六七八九十百千万两]+[章节回部卷]")
# 自研分析类文件名标记：拆解/报告/教程等天然包含"第N章"标题，不能用章节启发式误判为小说
_ANALYSIS_MARKER_RE = re.compile(
    r"拆|分析|报告|指南|教程|心得|笔记|研究|调研|扫榜|仿写|方法论|大纲|拆解|逐章|总结|决策|详案"
)
# 评论/方法论术语：小说正文几乎不出现，密集出现说明文本本身就是拆解/大纲/详案
_ANALYSIS_TERMS_RE = re.compile(
    r"拆解|拆书|拆文|分析|爽点|钩子|伏笔|节奏|大纲|卖点|追读|毒点|期待感|详案|"
    r"方法论|仿写|人设|金手指|题材|章尾|开篇"
)
# "书名 - 作者.txt" 全本命名（路径中允许空格变体，统一归一化后判定）
_BOOK_AUTHOR_RE = re.compile(r".+\s*-\s*.+\.txt$", re.IGNORECASE)
_ORIGIN_TEXT_RE = re.compile(r"原文", re.IGNORECASE)
_FRONT40_RE = re.compile(r"前\s*40\s*章", re.IGNORECASE)
_SINGLE_CHAPTER_RE = re.compile(r"第[0-9零一二三四五六七八九十百千万两]+章", re.IGNORECASE)


@dataclass(frozen=True)
class FileDecision:
    """单个候选文件的收录判定结果。"""

    path: Path
    included: bool
    reason: str


def _matches_novel_filename(path: Path) -> str | None:
    """按文件名模式识别小说全文/章节文本；命中返回原因，否则 None。"""
    name = path.name
    if name.endswith(".progress.json"):
        return "采集工具缓存（.progress.json，含整章 HTML 全文）"
    if path.suffix.lower() == ".txt":
        normalized = name
        if _BOOK_AUTHOR_RE.match(normalized):
            return "全本命名模式（书名 - 作者.txt）"
        if _ORIGIN_TEXT_RE.search(normalized):
            return "文件名含'原文'"
        if _FRONT40_RE.search(normalized):
            return "前 40 章章节文本"
        if _SINGLE_CHAPTER_RE.search(normalized):
            return "单章命名（第N章）"
    return None


def count_chapter_lines(text: str) -> int:
    """统计文本前部出现的行首章节标记数量（章节体小说判据，含 markdown 标题形态）。"""
    count = 0
    scanned = 0
    for line in text.splitlines():
        scanned += len(line) + 1
        if _CHAPTER_LINE_RE.match(line):
            count += 1
        if scanned >= CHAPTER_SCAN_BYTES:
            break
    return count


def count_analysis_terms(text: str) -> int:
    """统计文本前部评论/方法论术语命中次数（自研分析内容的内容侧判据）。"""
    count = 0
    scanned = 0
    for line in text.splitlines():
        scanned += len(line) + 1
        count += len(_ANALYSIS_TERMS_RE.findall(line))
        if scanned >= CHAPTER_SCAN_BYTES:
            break
    return count


def _has_analysis_path_marker(path: Path) -> bool:
    """文件名或任一父目录名命中自研分析标记（如 拆解/Untitled.md 受目录名保护）。"""
    return any(_ANALYSIS_MARKER_RE.search(part) for part in path.parts)


def decide(path: Path, *, read_text: bool = True) -> FileDecision:
    """对单个文件给出收录/排除判定；read_text=False 时跳过章节启发式（仅按元数据）。"""
    parts = set(path.parts)
    if "原文" in parts:
        return FileDecision(path, False, "位于'原文'目录")

    # 采集缓存等命名模式先于扩展名白名单判定，给出准确原因并防止未来扩展类型时漏网
    name_reason = _matches_novel_filename(path)
    if name_reason is not None:
        return FileDecision(path, False, name_reason)

    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        return FileDecision(path, False, f"v1 不支持的类型（仅 {sorted(SUPPORTED_SUFFIXES)}）")

    # 大小上限只拦 .txt（小说全本形态）；md/docx 的分析报告可能内嵌图片而超过 1MB
    if path.suffix.lower() == ".txt":
        try:
            size = path.stat().st_size
        except OSError as exc:
            return FileDecision(path, False, f"无法读取文件信息：{exc}")
        if size > MAX_FILE_BYTES:
            return FileDecision(path, False, f"txt 超过 1MB（{size} 字节），疑似小说全文")

    # 章节启发式：纯文本（md/txt）做行首扫描；
    # 自研分析内容两重豁免：①文件名/父目录命中分析标记；②内容评论术语密集；
    # 二者都不满足且章节行达标，才判为小说正文
    # （2026-09 dogfood：`## 第N章` md 全本旧规则零命中，真实拆解最低 11 次术语 vs 全本 2 次）
    if read_text and path.suffix.lower() in {".md", ".txt"} and not _has_analysis_path_marker(path):
        try:
            raw = path.read_bytes()[:CHAPTER_SCAN_BYTES]
        except OSError as exc:
            return FileDecision(path, False, f"无法读取文件：{exc}")
        text = raw.decode("utf-8", errors="ignore")
        chapter_lines = count_chapter_lines(text)
        if chapter_lines >= CHAPTER_LINE_THRESHOLD:
            term_hits = count_analysis_terms(text)
            if term_hits < ANALYSIS_TERM_MIN:
                return FileDecision(
                    path,
                    False,
                    f"章节体小说文本（检出 {chapter_lines} 个章节行 ≥ {CHAPTER_LINE_THRESHOLD}，"
                    f"评论术语 {term_hits} < {ANALYSIS_TERM_MIN}）",
                )

    return FileDecision(path, True, "收录：方法论/教学/素材类知识文件")
