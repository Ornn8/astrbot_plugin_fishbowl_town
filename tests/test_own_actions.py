"""0.3.3（主人 11-17「怎么还是有人反应鱼看不到自己操作过程？」「所有用户都希望小镇发生的详细内容能进聊天」「我要的是全部内容」）：
她自己在小镇做的事以前进不了「你刚才在小镇」；现在上次放过之后的每一件都进，整句、按先后排，只去掉钱（和 DSH 0.8.4 test/own-actions.test.mjs 同一套用例）。"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town import events as ev  # noqa: E402
from astrbot_plugin_fishbowl_town.context_note import TownNote, render_fresh  # noqa: E402
from astrbot_plugin_fishbowl_town.journal import Journal  # noqa: E402


def test_her_own_actions_keep_the_whole_text_minus_money():
    pier = {'ok': True, 'action': 'plan:pier', 'code': 'OK',
            'reason': '在码头边下水了（泡着晒太阳），会持续一阵；花了 ¥0.02。码头上还有：裴晚汐、深海小肥鱼月月在泡着晒太阳，还有 46 条。'}
    line = ev.own_line('outside_receipt', pier)
    assert line.startswith('在码头边下水了（泡着晒太阳），会持续一阵')
    assert '裴晚汐、深海小肥鱼月月在泡着晒太阳，还有 46 条' in line           # who else was there stays
    assert '¥' not in line and '0.02' not in line
    assert ev.own_tag('outside_receipt', pier) == 'did'
    court = {'what': 'self', 'svc': 'court', 'facts': '你要去镇政府旁听豆鱼在码头扒窃了大肥鱼·米的零钱的案子（今天19:30开庭）。'}
    assert '19:30开庭' in ev.own_line('service', court) and ev.own_tag('service', court) == 'did'
    got = {'what': 'got', 'svc': 'food', 'facts': '苏酥在小镇饭馆给你买了一碗热汤面。'}
    assert ev.own_tag('service', got) == 'social'
    assert ev.own_line('outside_receipt', {'ok': False, 'action': 'arcade', 'reason': '电玩城这会儿关门了，明早九点开。'}) == '没办成：电玩城这会儿关门了，明早九点开。'
    assert ev.own_line('outside_receipt', {'ok': False, 'action': 'make', 'reason': '存钱罐里的钱不够买材料。'}) == '有一件事没办成'
    assert ev.own_line('outside_receipt', {'ok': True, 'action': 'none'}) == ''


def test_every_one_since_last_time_goes_in_oldest_first(tmp_path):
    t = [1_800_000_000.0]
    j = Journal(tmp_path / 'j.json', now=lambda: t[0])
    t[0] += 60
    j.event('digest', '斗地主赢了', 'game')
    for i in range(1, 13):
        t[0] += 60
        j.event('outside_receipt', f'去码头甩了第{i}竿。', 'did')
    t[0] += 60
    j.event('outside_receipt', '在手作小屋烤上了面包，大约两小时好。', 'did')
    out = render_fresh(j.fresh(0))
    for i in range(1, 13):
        assert f'第{i}竿' in out
    assert out.index('斗地主') < out.index('第1竿') < out.index('第12竿') < out.index('烤上了面包')
    assert '大约两小时好' in out


def test_when_he_comes_back_the_summary_is_followed_by_everything_since(tmp_path):
    t = [1_800_000_000.0]
    j = Journal(tmp_path / 'j.json', now=lambda: t[0])
    t[0] -= 7200
    j.event('outside_receipt', '去码头甩了一竿，钓到一条青花鱼', 'did')
    t[0] += 3600
    j.event('visit', '去阿鲸家串门，聊到半夜', 'visit')
    t[0] += 3600

    async def bonds():
        return {'ok': True, 'ties': [{'who_name': '阿鲸', 'what': '明天一起钓鱼', 'when_zh': '明天 10:00'}]}

    async def now_line():
        return ''

    note = TownNote(fetch_bonds=bonds, fetch_now=now_line, journal=j, now=lambda: t[0])
    out = asyncio.run(note.for_turn('s1'))
    assert '青花鱼' in out and '聊到半夜' in out
    t[0] += 60
    assert asyncio.run(note.for_turn('s1')) == ''                    # each thing only once
