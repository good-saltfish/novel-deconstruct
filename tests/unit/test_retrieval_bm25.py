"""BM25 索引测试：排序正确性、受控词加权、并列确定性、排除集合、JSON 往返。"""

import json

from ndecon.retrieval.bm25 import (
    CONTROLLED_TERM_BOOST,
    BM25Index,
    IndexedDocument,
    controlled_terms,
)


def _doc(doc_id: str, text: str, *, order: int | None = None, title: str = "") -> IndexedDocument:
    """构造最小索引文档。"""
    return IndexedDocument(doc_id=doc_id, doc_type="chapter_summary", order=order, title=title, text=text)


def test_term_match_ranks_above_no_match() -> None:
    """含查询词的文档必须排在零命中文档之前。"""
    index = BM25Index([_doc("a", "毫不相干的内容"), _doc("b", "影兽爬出井口")])
    hits = index.search("影兽")
    assert [hit.doc_id for hit in hits] == ["b"]
    assert hits[0].score > 0


def test_term_frequency_and_length_prefers_focused_doc() -> None:
    """短文档里重复命中比长文档里单次命中分高（BM25 长度归一）。"""
    short = _doc("short", "灯阵 灯阵 灯阵")
    long = _doc("long", "灯阵 " + "闲话 " * 100)
    index = BM25Index([long, short])
    hits = index.search("灯阵")
    assert hits[0].doc_id == "short"


def test_zero_score_docs_excluded() -> None:
    """与查询没有任何共同 token 的文档不返回。"""
    index = BM25Index([_doc("a", "路灯"), _doc("b", "井水")])
    assert [hit.doc_id for hit in index.search("影兽")] == []


def test_tie_break_is_insertion_order() -> None:
    """完全相同的文本得平分时，按加入索引的先后次序排列，不可随机。"""
    docs = [_doc(str(i), "同样的灯阵文字") for i in range(5)]
    index = BM25Index(docs)
    first = [hit.doc_id for hit in index.search("灯阵", k=3)]
    second = [hit.doc_id for hit in index.search("灯阵", k=3)]
    assert first == second == ["0", "1", "2"]


def test_exclude_ids() -> None:
    """exclude_ids 中的文档（如本章自己）即使命中也不返回。"""
    index = BM25Index([_doc("a", "灯阵"), _doc("b", "灯阵")])
    assert [hit.doc_id for hit in index.search("灯阵", exclude_ids={"a"})] == ["b"]


def test_controlled_terms_cover_enums_and_boost_query() -> None:
    """受控词表覆盖基调/主题枚举；含标签的查询相对普通词加权。"""
    terms = controlled_terms()
    assert terms["复仇"] == CONTROLLED_TERM_BOOST
    assert terms["热血"] == CONTROLLED_TERM_BOOST

    doc = _doc("a", "复仇 复仇 复仇")
    boosted = BM25Index([doc]).search("复仇")[0].score
    plain = BM25Index([doc], term_boost={}).search("复仇")[0].score
    assert boosted > plain


def test_json_roundtrip_preserves_ranking(tmp_path) -> None:
    """序列化再加载后，语料、参数与排序结果完全一致。"""
    docs = [
        _doc("a", "影兽夜袭配电站", order=1),
        _doc("b", "主角买菜做饭", order=2),
        _doc("c", "灯阵守住电闸影兽败退", order=3),
    ]
    index = BM25Index(docs)
    path = tmp_path / "index.json"
    index.dump_json(path)
    reloaded = BM25Index.load_json(path)

    assert reloaded.k1 == index.k1 and reloaded.b == index.b
    before = [hit.model_dump() for hit in index.search("影兽 灯阵")]
    after = [hit.model_dump() for hit in reloaded.search("影兽 灯阵")]
    assert before == after

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["doc_count"] == 3
    assert payload["documents"][0]["doc_id"] == "a"


def test_unknown_version_rejected() -> None:
    """未知索引版本必须报错，拒绝用不兼容语料构建。"""
    try:
        BM25Index.from_dict({"version": 999, "documents": [], "k1": 1.5, "b": 0.75})
    except ValueError:
        return
    raise AssertionError("未知版本应当被拒绝")


def test_empty_index_and_empty_query() -> None:
    """空索引与空查询都安全返回空结果。"""
    assert BM25Index([]).search("灯阵") == []
    assert BM25Index([_doc("a", "灯阵")]).search("，，，") == []
