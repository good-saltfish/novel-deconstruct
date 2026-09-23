"""quote 定位器测试：精确/空白偏差/标点偏差/歧义/过短保护。"""

from ndecon.providers.locate import locate_quote


def test_exact_unique_match() -> None:
    """唯一精确命中应返回正确区间。"""
    chapter = "他走进院子，短刀在月光下泛着冷光，随后转身离开。"
    quote = "短刀在月光下泛着冷光"
    start, end = locate_quote(chapter, quote)
    assert chapter[start:end] == quote


def test_whitespace_variant_match() -> None:
    """模型 quote 与原文只有空白/NFKC 差异时应宽松命中。"""
    chapter = "他低声说： “我们在看着你。” 随后灯灭了。"
    quote = "我们在看着你。"
    span = locate_quote(chapter, quote)
    assert span is not None
    start, end = span
    assert chapter[start:end] == "我们在看着你。"


def test_punctuation_variant_match() -> None:
    """模型漏掉标点时通过第三级去标点滑窗命中（quote 足够长）。"""
    chapter = "雨夜，他推开了那扇破旧的木门，风声灌了进来。"
    quote = "他推开了那扇破旧的木门风声灌了进来"  # 去标点后应为唯一子串
    span = locate_quote(chapter, quote)
    assert span is not None
    start, end = span
    assert "木门" in chapter[start:end]


def test_ambiguous_match_returns_none() -> None:
    """同一 quote 在原文出现多次（位置歧义）时返回 None，不随机锚定。"""
    chapter = "他来了。她走了。他来了。天快亮了。"
    assert locate_quote(chapter, "他来了。") is None


def test_missing_quote_returns_none() -> None:
    """原文中根本不存在的 quote 必须返回 None。"""
    assert locate_quote("院子里很安静。", "根本不存在的句子啊啊啊") is None


def test_empty_quote_returns_none() -> None:
    """空 quote 直接拒绝。"""
    assert locate_quote("正文", "") is None
    assert locate_quote("正文", "   ") is None
