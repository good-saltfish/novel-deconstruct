"""中文 bigram 分词器测试：NFKC 归一、bigram 滑窗、英文数字串、确定性。"""

from ndecon.retrieval.tokenize import normalize_text, tokenize


def test_cjk_bigrams() -> None:
    """连续汉字按字两两滑窗；两字词天然成为单个 bigram。"""
    assert tokenize("路灯") == ["路灯"]
    assert tokenize("路灯阵") == ["路灯", "灯阵"]
    assert tokenize("长街灯阵") == ["长街", "街灯", "灯阵"]


def test_single_cjk_char_kept() -> None:
    """夹在非汉字中的单个汉字不能丢，退化为单字 token。"""
    assert tokenize("第3章") == ["第", "3", "章"]


def test_ascii_and_digits_lowercased() -> None:
    """英文字母/数字串整体保留并小写。"""
    assert tokenize("BM25 ok") == ["bm25", "ok"]


def test_nfkc_fullwidth_normalization() -> None:
    """全角字母/数字与标点经 NFKC 归一为半角，全角空格不产生 token。"""
    assert normalize_text("ＬＩＧＨＴ１２３") == "LIGHT123"
    assert tokenize("ＬＩＧＨＴ") == ["light"]
    assert tokenize("１２３") == ["123"]


def test_punctuation_and_whitespace_dropped() -> None:
    """中英文标点与空白只充当片段分隔，不出现在 token 里。"""
    assert tokenize("路灯，结阵！light...") == ["路灯", "结阵", "light"]
    assert tokenize("a，b") == ["a", "b"]


def test_deterministic() -> None:
    """同输入多次切词结果逐位一致。"""
    text = "第十章 万灯齐明：影兽被逼回井底？"
    assert tokenize(text) == tokenize(text)


def test_empty_text() -> None:
    """空串与纯标点都切为空列表。"""
    assert tokenize("") == []
    assert tokenize("，。！？……") == []
