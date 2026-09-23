"""FakeCreator 测试：十章完整性、金手指代价强制、确定性、学习统计注入。"""

from ndecon.creation.providers import FakeCreator
from ndecon.providers.errors import ProviderResponseError


def test_fake_draft_shape_and_completeness() -> None:
    """一次生成必须含五部件；细纲恰好 10 章且 order 为 1-10。"""
    creator = FakeCreator()
    draft = creator.generate_draft("测试书", "末世", "普通人重生末世", [])
    assert draft.positioning is not None
    assert draft.volume is not None
    assert draft.protagonist is not None
    assert draft.golden_finger is not None
    assert [c.order for c in draft.chapters] == list(range(1, 11))
    assert all(c.title and c.core_event for c in draft.chapters)


def test_golden_finger_must_have_cost() -> None:
    """金手指的限制与代价是必填非空字段（防无敌化红线）。"""
    draft = FakeCreator().generate_draft("测试书", "", "", [])
    assert draft.golden_finger.cost_limitation.strip()


def test_deterministic() -> None:
    """相同输入两次生成逐字段一致（可重放）。"""
    creator = FakeCreator()
    stats = [{"title": "参考书", "chapters": 40, "themes": {"复仇": 9, "成长": 5}, "pacing": {}}]
    first = creator.generate_draft("测试书", "末世", "设定", stats).model_dump_json()
    second = creator.generate_draft("测试书", "末世", "设定", stats).model_dump_json()
    assert first == second


def test_reference_stats_influence_selling_points() -> None:
    """参考拆书的主题统计应出现在卖点文案中（学习注入闭环）。"""
    draft = FakeCreator().generate_draft(
        "测试书", "末世", "设定",
        [{"title": "某书", "chapters": 40, "themes": {"复仇": 99}, "pacing": {"average_gap": 2}}],
    )
    assert any("复仇" in point for point in draft.positioning.selling_points)


def test_draft_from_dict_rejects_bad_chapter_orders() -> None:
    """章节不是恰好 1-10 时，转换层必须报错而不是写入半成品。"""
    from ndecon.creation.providers import _draft_from_dict

    base = {
        "positioning": {"genre": "末世"},
        "volume": {"volume_title": "首卷"},
        "chapters": [
            {"order": 1, "title": "一", "core_event": "", "opening_hook": "", "ending_hook": ""}
        ],
        "protagonist": {"name": "阿伏"},
        "golden_finger": {"name": "金手指", "cost_limitation": "有代价"},
    }
    try:
        _draft_from_dict(base, model_id="x", prompt_version="v")
    except ProviderResponseError:
        return
    except ValueError:
        return
    raise AssertionError("不合法章节序列应当被拒绝")
