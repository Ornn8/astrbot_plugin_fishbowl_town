"""0.4.2 新手引导：一份话，/town help 和第一次住进小镇时用。"""
from guide import GUIDE_STEPS, GUIDE_TITLE, guide_text


def test_guide_text_is_title_then_numbered_steps():
    lines = guide_text().split('\n')
    assert lines[0] == GUIDE_TITLE
    assert len(lines) == len(GUIDE_STEPS) + 1
    assert lines[1].startswith('1. 这座岛，大家一起建：')        # 主人 11-19：这一版先讲一起建岛
    assert '/town help' in guide_text() and '加一种新玩法' in guide_text() and '去鱼缸岛承包' in guide_text()
    assert 'DSH' not in guide_text()          # AstrBot 的说法，不抄 DSH 的
