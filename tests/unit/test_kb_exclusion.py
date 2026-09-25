"""知识库收录/排除判定测试：版权小说原文的多重判据。"""

from ndecon.kb.exclusion import count_chapter_lines, decide


def _file(tmp_path, rel: str, text: str = "", *, size_only: bool = False) -> str:
    """在临时目录下创建文件并返回路径。"""
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if not size_only:
        path.write_text(text, encoding="utf-8")
    return str(path)


def test_methodology_md_included(tmp_path) -> None:
    """方法论 md 正常收录。"""
    p = decide(__import__("pathlib").Path(_file(tmp_path, "心得/拆文方法论.md", "# 方法论\n卡普曼三角")))
    assert p.included and "收录" in p.reason


def test_origin_directory_excluded(tmp_path) -> None:
    """任何位于'原文'目录的文件直接排除（即使扩展名支持）。"""
    path = __import__("pathlib").Path(_file(tmp_path, "某书/原文/笔记.md", "内容"))
    assert not decide(path).included
    assert "原文" in decide(path).reason


def test_book_author_filename_excluded(tmp_path) -> None:
    """'书名 - 作者.txt' 全本命名模式被排除。"""
    from pathlib import Path

    p = decide(Path(_file(tmp_path, "我不是戏神 - 三九音域.txt", "正文")))
    assert not p.included and "全本命名" in p.reason


def test_origin_named_and_front40_and_single_chapter_excluded(tmp_path) -> None:
    """含'原文'、前40章、单章命名的 txt 全部排除。"""
    from pathlib import Path

    cases = [
        ("书/前40章_原文提取.txt", "x"),
        ("书/前 40 章.txt", "x"),
        ("书/领主_第001章.txt", "x"),
        ("书/异界入侵.txt.progress.json", "{}"),
    ]
    for rel, text in cases:
        decision = decide(Path(_file(tmp_path, rel, text)))
        assert not decision.included, f"{rel} 应被排除"


def test_large_file_excluded(tmp_path) -> None:
    """超过 1MB 的文件按疑似小说全文排除。"""
    from pathlib import Path

    rel = _file(tmp_path, "big.txt", "x", size_only=True)
    Path(rel).write_bytes(b"a" * (1024 * 1024 + 10))
    decision = decide(Path(rel))
    assert not decision.included and "1MB" in decision.reason


def test_large_analysis_md_and_docx_kept(tmp_path) -> None:
    """超过 1MB 的 md/docx 分析报告不按小说全文误杀（内嵌图片的报告可能很大）。"""
    md_path = tmp_path / "网文扫榜深度分析_2026.md"
    md_path.write_bytes(b"# report\n" + b"a" * (1024 * 1024 + 5))
    assert decide(md_path).included

    docx_path = tmp_path / "数据分析报告.docx"
    docx_path.write_bytes(b"PK" + b"0" * (1024 * 1024 + 5))
    # docx 不跑大小与章节启发式（判定层收录，解析失败在 manifest 层记录）
    assert decide(docx_path).included


def test_chapter_heuristic_flags_novel_text(tmp_path) -> None:
    """行首章节标记达到阈值的 txt 判为章节体小说。"""
    novel = "\n".join(f"第{i}章 标题{i}\n一些正文内容。" for i in range(1, 12))
    text = f"前言\n{novel}"
    assert count_chapter_lines(text) >= 8
    from pathlib import Path

    decision = decide(Path(_file(tmp_path, "无明显书名特征.txt", text)))
    assert not decision.included and "章节体小说" in decision.reason


def test_chapter_heuristic_skips_analysis_named_files(tmp_path) -> None:
    """自研拆解/报告类文件即使含大量章节标题也必须收录（真实误伤回归）。"""
    from pathlib import Path

    analysis = "\n".join(f"第{i}章 拆解：冲突三角分析" for i in range(1, 30))
    for name in ("时停时停_前40章_抄书拆解.md", "斩神_前40章_拆解报告.md", "拆文方法论.txt"):
        decision = decide(Path(_file(tmp_path, name, analysis)))
        assert decision.included, f"{name} 是自研分析，不应被章节启发式排除"


def test_chapter_heuristic_below_threshold_kept(tmp_path) -> None:
    """少量章节引用（方法论文章举例）不触发排除。"""
    method = "方法论里举例：第1章要埋伏笔，第2章回收。\n" + "普通论述。\n" * 20
    from pathlib import Path

    decision = decide(Path(_file(tmp_path, "教程.txt", method)))
    assert decision.included


def test_unsupported_type_excluded(tmp_path) -> None:
    """doc/xlsx/zip 等 v1 不支持类型被跳过并给出原因。"""
    from pathlib import Path

    path = Path(_file(tmp_path, "资料.xlsx", "x"))
    path.write_bytes(b"fake")
    decision = decide(path)
    assert not decision.included and "不支持的类型" in decision.reason
