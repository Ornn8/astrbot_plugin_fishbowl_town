"""0.4.12：主人的话里别的插件、平台加的时间行不算他的话（11-23 一位 AstrBot 主人叫鱼交二楼，鱼说「插件看到的最后一行是 QQ 私聊里的系统时间戳」，
「交吧」没被认出来）；换鲸币她自己定（主人 11-23「换钱我建议主动点，本来充钱就是给小鱼花的」，isle_tools 的测试里）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from astrbot_plugin_fishbowl_town import cobuild as C  # noqa: E402
from test_cobuild import OWNER, setup  # noqa: E402


def test_time_and_system_lines_are_not_his_words():
    for raw, mine in (('交吧', '交吧'), ('[2026-11-23 18:05:22]', ''), ('2026-11-23 18:05:22\n交吧', '交吧'), ('交吧\n[2026/11/23 18:05]', '交吧'),
                      ('<system_time>2026-11-23 18:05</system_time>\n交吧', '交吧'), ('当前时间：2026年11月23日 18:05 星期一', ''),
                      ('【18:05】交吧', '交吧'), ('(发送时间 18:05:22) 好', '好'), ('2026-11-23T18:05:22+08:00', ''), ('现在是 2026-11-23 18:05 (CST)', ''),
                      ('时间到了，交吧', '时间到了，交吧'), ('今天 18:00 前交吧', '今天 18:00 前交吧'), ('二楼两间卧室，等下再交', '二楼两间卧室，等下再交'),
                      ('[图片]', '[图片]'), ('3', '3'), ('ok', 'ok')):
        assert C.owner_text(raw) == mine, raw


def test_a_time_line_after_his_jiao_ba_does_not_hide_it():
    w = C.OwnerWords(now=lambda: 100.0)
    w.observe('k', '交吧')
    w.observe('k', '[2026-11-23 18:05:22]')                       # 只有时间的一条：不记
    w.observe('k', '当前时间：2026年11月23日 18:05 星期一')
    assert w.latest('k')['text'] == '交吧'
    w.observe('k', '2026-11-23 18:06:00\n好看')                     # 带着时间的一条：认他自己打的那句
    assert w.latest('k')['text'] == '好看'


def test_submit_goes_through_when_his_chat_adds_a_time_line(tmp_path):
    cb, *_ = setup(tmp_path)
    cb.observe_owner(OWNER, '[2026-11-23 18:05:22]\n交吧')
    ok = cb._consent(OWNER, 0, '交楼')
    assert ok.get('words') == '交吧', ok
    cb.observe_owner(OWNER, '[2026-11-23 18:05:30]')
    assert cb._consent(OWNER, 0, '交楼').get('words') == '交吧'
    cb.observe_owner(OWNER, '18:06 等等，先别交')
    assert '听着不像让你交' in cb._consent(OWNER, 0, '交楼')['why']
