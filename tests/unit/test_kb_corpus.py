"""知识库建库/检索端到端测试：混合目录、审计清单、版权负面断言、落盘往返。"""

import json
from pathlib import Path

from ndecon.kb.corpus import (
    INDEX_FILENAME,
    MANIFEST_FILENAME,
    build_knowledge_index,
    knowledge_brief,
    search_knowledge,
    write_knowledge_artifacts,
)

SECRET_NOVEL_LINE = "版权密语陆沉周伯陈主任林盏铜灯影兽第九百九十九章"


def _build_mixed_library(root: Path) -> None:
    """构造混合资料库：方法论 md/docx + 小说全文/前40章/原文目录/不支持类型。"""
    (root / "拆解").mkdir(parents=True)
    (root / "拆解" / "拆文方法论_刀刀烈火.md").write_text(
        "# 卡普曼三角冲突\n"
        "每个冲突都有迫害者、受害者、拯救者三个角色。开篇五百字内必须有一个三角开转。\n\n"
        "# 情绪轨迹\n"
        "先抑后扬是 v 型情绪轨迹，压抑到底再爆发，金手指必须当场付出代价。\n",
        encoding="utf-8",
    )
    (root / "设定和素材").mkdir(parents=True)
    (root / "设定和素材" / "法医学术语解释.txt").write_bytes("法医术语：尸僵与死亡时间判断。".encode("gbk"))

    # C 类：小说全本、前40章、原文目录、单章、采集缓存、超大文件
    (root / "我不是戏神 - 三九音域.txt").write_text(
        SECRET_NOVEL_LINE + "\n" + "\n".join(f"第{i}章 章节内容" for i in range(1, 30)),
        encoding="utf-8",
    )
    (root / "某书").mkdir()
    (root / "某书" / "前40章_原文提取.txt").write_text(
        "\n".join(f"第{i}章 内容{SECRET_NOVEL_LINE}" for i in range(1, 20)), encoding="utf-8"
    )
    (root / "神域" / "原文").mkdir(parents=True)
    (root / "神域" / "原文" / "神域囚徒！_前40章_原文.txt").write_text(SECRET_NOVEL_LINE, encoding="utf-8")
    (root / "异界入侵？一把抓住炼化成游戏！ - 蒸汽饭桶.txt.progress.json").write_text(
        f"<article>{SECRET_NOVEL_LINE}</article>", encoding="utf-8"
    )
    (root / "榜单.xlsx").write_bytes(b"not-really-xlsx")
    (root / "超大资料.txt").write_bytes("章".encode() * 400000)


def test_mixed_library_index_excludes_all_novels(tmp_path) -> None:
    """小说全文/前40章/原文目录/缓存/大文件全部排除，方法论与 GBK 素材收录。"""
    library = tmp_path / "lib"
    _build_mixed_library(library)

    index, manifest = build_knowledge_index([library])
    included = {item.relative_path for item in manifest if item.included}
    excluded = {item.relative_path: item.reason for item in manifest if not item.included}

    assert any("拆文方法论_刀刀烈火.md" in path for path in included)
    assert any("法医学术语解释.txt" in path for path in included)

    secret_files = [
        "我不是戏神 - 三九音域.txt",
        "前40章_原文提取.txt",
        "神域囚徒！_前40章_原文.txt",
        "异界入侵？一把抓住炼化成游戏！ - 蒸汽饭桶.txt.progress.json",
        "超大资料.txt",
    ]
    for secret in secret_files:
        matched = [path for path in excluded if path.endswith(secret)]
        assert matched, f"{secret} 必须被排除"

    # xlsx 被跳过但有明确原因
    assert any(path.endswith("榜单.xlsx") for path in excluded)

    # 版权特征串不出现在任何索引语料中（含序列化结果）
    serialized = json.dumps(index.to_dict(), ensure_ascii=False)
    assert SECRET_NOVEL_LINE not in serialized
    assert "版权密语" not in serialized


def test_artifacts_written_and_searchable(tmp_path) -> None:
    """落盘后可加载检索；manifest 与 index 文件齐备且可审计。"""
    library = tmp_path / "lib"
    workspace = tmp_path / "ws"
    _build_mixed_library(library)
    index, manifest = build_knowledge_index([library])
    directory = write_knowledge_artifacts(workspace, [library], index, manifest)

    assert (directory / INDEX_FILENAME).is_file()
    assert (directory / MANIFEST_FILENAME).is_file()

    snippets = search_knowledge(workspace, "卡普曼三角 迫害者 拯救者")
    assert snippets, "方法论内容应被命中"
    assert any("卡普曼三角" in s.text for s in snippets)
    assert all(s.source for s in snippets)
    # 命中片段带来源且不含版权特征串
    serialized = json.dumps([s.model_dump() for s in snippets], ensure_ascii=False)
    assert "版权密语" not in serialized

    manifest_data = json.loads((directory / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert manifest_data["included_count"] >= 2
    assert manifest_data["excluded_count"] >= 5
    assert all("reason" in item for item in manifest_data["files"])


def test_search_without_index_returns_empty(tmp_path) -> None:
    """未建库的工作区安全降级为空结果。"""
    assert search_knowledge(tmp_path / "no-kb", "任何查询") == []
    assert knowledge_brief([]) == ""


def test_knowledge_brief_cites_sources(tmp_path) -> None:
    """注入骨架的方法论区块标注来源文件。"""
    library = tmp_path / "lib"
    workspace = tmp_path / "ws"
    _build_mixed_library(library)
    index, manifest = build_knowledge_index([library])
    write_knowledge_artifacts(workspace, [library], index, manifest)

    snippets = search_knowledge(workspace, "金手指 代价 情绪轨迹")
    brief = knowledge_brief([s.model_dump() for s in snippets])
    assert "写作方法论参考" in brief
    assert "来源" in brief
