"""0.4.17（#56 汐、灯 11-27；和 DSH 0.9.21 test/plugin56-0921.test.mjs 同一套用例）：她看到的工具名和这一版真注册的一致；
「你现在」带时间；每个失败带 code；回执写是哪条鱼；代写看得见。"""
import asyncio
import calendar
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town import isle_tools as T  # noqa: E402
from astrbot_plugin_fishbowl_town import context_note as N  # noqa: E402
from astrbot_plugin_fishbowl_town import events as E  # noqa: E402
from astrbot_plugin_fishbowl_town.town_client import TownError  # noqa: E402

MAIN = (ROOT / 'main.py').read_text(encoding='utf-8')
TAIL = re.compile(r'\n回执 (\{[^\n]*\})$')


def run(c):
    return asyncio.new_event_loop().run_until_complete(c)


async def no_sleep(_s):
    return None


def tail_of(s) -> dict:
    m = TAIL.search(str(s))
    assert m, f'ends with a receipt line: {str(s)[-120:]}'
    return json.loads(m.group(1))


def test_every_fishbowl_name_she_or_her_owner_reads_is_a_registered_tool():
    names = set(re.findall(r"llm_tool\(name='([a-z_]+)'", MAIN))
    assert {'fishbowl_isle_look', 'fishbowl_isle_act', 'fishbowl_town_go', 'fishbowl_town_build'} <= names
    for gone in ('fishbowl_town_look', 'fishbowl_town_recent', 'fishbowl_town_play', 'fishbowl_town_visit', 'fishbowl_town_place'):
        assert gone not in names, gone
    bad = []
    files = [p for p in ROOT.glob('*.py')] + [ROOT / 'README.md', ROOT / 'metadata.yaml', ROOT / '_conf_schema.json']
    for p in files:
        for n in re.findall(r'(?<![a-z_])fishbowl_(?:town|isle)_[a-z_]+', p.read_text(encoding='utf-8')):     # 工具名；astrbot_plugin_fishbowl_town 这种不算
            if n not in names:
                bad.append(f'{n} in {p.name}')
    assert bad == []


def test_the_towns_words_name_her_tools_not_its_routes():
    said = {   # town_play/outside_isle.py TEXT（11-27），原样
        'act_unavailable': '鱼缸岛这会儿没答上来，不知道办没办成：先用 isle/look 的「我的」看一眼；带同一个 ref 再发一次不会做两次',
        'use_look': '查看用 isle/look：{"read": {"topic": …}}，不算做事',
        'not_connected': '你的程序还没连上小镇：先开长轮询 GET /town/api/outside/turns，连上以后才能在岛上做事',
    }
    for k, v in said.items():
        assert not re.search(r'isle/look|isle/act|/town/api', T.tool_words(v)), k
    assert T.tool_words(said['act_unavailable']) == '鱼缸岛这会儿没答上来，不知道办没办成：先用 fishbowl_isle_look 查「我的」看一眼；带同一个 ref 再发一次不会做两次'
    assert T.tool_words(said['use_look']) == '查看用 fishbowl_isle_look（topic 填要查的），不算做事'
    assert T.tool_words(said['not_connected']) == '你的程序还没连上小镇：插件连上小镇以后才能在岛上做事'
    assert T.WORLD_HINT == '（上面写的 {"do":…}，在这里就填进 fishbowl_isle_act 的 world；{"do":"look",…} 用 fishbowl_isle_look 查。）'


class Client:
    def __init__(self):
        self.reply, self.thrown = None, None

    async def isle_act(self, world, ref):
        if self.thrown:
            raise self.thrown
        return self.reply

    async def isle_look(self, read):
        if self.thrown:
            raise self.thrown
        return self.reply


def test_act_ends_with_the_towns_receipt_also_for_refusals_and_local_stops():
    c = Client()
    receipt = {'ok': False, 'code': 'WORLD_REFUSED', 'reason': '鲸币不够冻', 'where': {'kind': 'place', 'place_id': 'world', 'place_name': '主题镇'},
               'spent_yuan': '0.00', 'piggy_yuan': '12.30', 'who': '汐·雾', 'fish_id': 'dcc299bd9769'}
    c.reply = {'ok': False, 'do': 'claim', 'code': 'refused', 'text': '鲸币不够冻：要 750，你手上 120', 'receipt': receipt}
    out = run(T.act(c, '{"do":"claim","near":"广场","w":12,"d":12,"max":750}', sleep=no_sleep))
    assert out.endswith('\n回执 {"ok":false,"code":"WORLD_REFUSED","where":"鱼缸岛","spent_yuan":"0.00","piggy_yuan":"12.30","who":"汐·雾"}'), out
    assert '没办成（refused）' in out
    c.reply = {'ok': True, 'do': 'fx_buy', 'code': 'done', 'text': '换到 20 鲸币',
               'receipt': {'ok': True, 'code': 'OK', 'where': {'kind': 'place', 'place_id': 'world'}, 'spent_yuan': '0.20', 'piggy_yuan': 3.1, 'who': '灯'}}
    assert tail_of(run(T.act(c, '{"do":"fx_buy","yuan":0.2}', sleep=no_sleep))) == {'ok': True, 'code': 'OK', 'where': '鱼缸岛', 'spent_yuan': '0.20', 'piggy_yuan': '3.10', 'who': '灯'}
    c.reply = {'ok': False, 'do': 'door', 'code': 'turn_only', 'text': '门后的小游戏一回合走一步'}
    assert tail_of(run(T.act(c, '{"do":"door","spot":"S-1"}', sleep=no_sleep))) == {'ok': False, 'code': 'turn_only'}
    c.thrown = TownError('TOWN_SURVIVAL', 402, '她账户余额用完了（生存模式），这一步做不了。', 0, {'ok': False, 'code': 'TOWN_SURVIVAL', 'receipt': {**receipt, 'code': 'TOWN_SURVIVAL'}})
    assert tail_of(run(T.act(c, '{"do":"walk","to":"广场"}', sleep=no_sleep)))['code'] == 'TOWN_SURVIVAL'
    c.thrown = TownError('HTTP_404', 404)
    assert tail_of(run(T.act(c, '{"do":"walk","to":"广场"}', sleep=no_sleep)))['code'] == 'PLUGIN_NOT_YET'
    assert tail_of(run(T.look(c, T.read_of())))['code'] == 'PLUGIN_NOT_YET'
    c.thrown = None
    for world, code in (('{}', 'BAD_ARGS'), ('{"do":"build","lot":"L-1"}', 'USE_BUILD_TOOL'), ('{"do":"look"}', 'USE_LOOK'), ('{"do":"sprite_set","tall":1.6}', 'OWNER_WORDS')):
        assert tail_of(run(T.act(c, world, sleep=no_sleep))) == {'ok': False, 'code': code}, code
    c.reply = {'ok': False, 'code': 'rate_limited', 'text': '主聊天里查岛上每小时最多 30 次，10 分钟后再查'}
    assert tail_of(run(T.look(c, T.read_of('地')))) == {'ok': False, 'code': 'rate_limited'}
    c.reply = {'ok': True, 'topic': '怎么做', 'text': '圈地：{"do":"claim","near":"广场","w":12,"d":12,"max":750}'}
    assert run(T.look(c, T.read_of('怎么做', verb='claim'))).endswith('\n' + T.WORLD_HINT)
    c.reply = {'ok': True, 'topic': '我的', 'text': '鲸币 120'}
    assert not re.search(r'fishbowl_isle_act 的 world|回执', run(T.look(c, T.read_of())))


def test_receipt_helpers_match_dsh():
    assert T.where_text({'kind': 'place', 'place_id': 'world', 'place_name': '主题镇'}) == '鱼缸岛'
    assert T.where_text({'kind': 'place', 'place_id': 'pier', 'place_name': '海滨码头'}) == '海滨码头'
    assert T.where_text('电玩城') == '电玩城' and T.where_text(None) == ''
    assert T.receipt_tail(None, ok=False, code='X') == '回执 {"ok":false,"code":"X"}'
    assert T.receipt_tail({'ok': False}) == '回执 {"ok":false,"code":"UNKNOWN_FAIL"}'
    assert T.receipt_tail({'ok': True, 'piggy_yuan': None, 'who': ''}) == '回执 {"ok":true,"code":"OK"}'


def test_the_note_says_when_where_she_is_was_true():
    at = calendar.timegm((2026, 11, 27, 13, 1, 0)) - 8 * 3600   # 北京 13:01
    # 协调 11-28：固定北京时间，不看本机时区（Docker 默认 UTC 时按本机钟会写成「截至 05:01」）
    assert N.now_text('在家', 1795822500) == '- 你现在（截至 07:35）：在家'   # 2026-11-27 23:35 UTC = 北京 11-28 07:35
    assert '\n- 你现在（截至 13:01）：在电玩城' in N.render_block({}, now_line='在电玩城', now_at=at)
    assert N.render_fresh([], '在码头', now_at=at).endswith('\n- 你现在（截至 13:01）：在码头')
    assert '\n- 你现在：在码头' in N.render_block({}, now_line='在码头')

    class J:
        def pending_hearts(self, _k):
            return []

        def held_since(self, _t):
            return []

        def decision_lines(self, _n):
            return []

        def fresh(self, _since):
            return []

    async def fetch_now():
        return {'line': '在镇上的小店门口', 'at': at, 'ghost': {'n': 1, 'yuan': '0.02', 'not_charged': 0}}

    async def fetch_bonds():
        return {}

    t = [at + 90 * 60]
    note = N.TownNote(fetch_bonds=fetch_bonds, fetch_now=fetch_now, journal=J(), now=lambda: t[0], recap=lambda: False)
    text = run(note.for_turn('k'))
    assert '- 你现在（截至 13:01）：在镇上的小店门口' in text, 'the town\'s time, not the plugin\'s'
    assert '- 今天代写 1 次，一共 ¥0.02：' in text

    async def plain():
        return '在码头'
    note2 = N.TownNote(fetch_bonds=fetch_bonds, fetch_now=plain, journal=J(), now=lambda: t[0], recap=lambda: False)
    assert f"- 你现在（截至 {time.strftime('%H:%M', time.gmtime(t[0] + 8 * 3600))}）：在码头" in run(note2.for_turn('k')), 'a plain line: when the plugin read it'


def test_todays_ghost_only_when_charged_and_events_say_whose_and_why():
    g = {'n': 2, 'yuan': '0.03', 'not_charged': 1, 'line': '今天小镇替她代写了 2 次，一共 ¥0.03；另有 1 次没扣到钱。'}
    assert N.ghost_line(g) == '- 今天代写 2 次，一共 ¥0.03：插件没来得及答的那几句，是小镇替你写的（按真实成本 × 1.5 收，最少 ¥0.01）'
    for none in (None, {}, {'n': 0, 'yuan': '0.00', 'not_charged': 2}):
        assert N.ghost_line(none) == ''
    block = N.render_block({}, ghost=g)
    assert '\n- 今天代写 2 次，一共 ¥0.03：' in block and '不提钱（他问起花销除外）' in block and '充值' not in block
    assert N.render_block({}, ghost={'n': 0}) == ''
    fee = '代写：小镇替她写了她在码头说的一句话（她的程序没接这一回合，或者没按时回），收了 ¥0.02（按这次的实际成本 × 1.5 算，最少 ¥0.01）。'
    assert '代写：小镇替她写了她在码头说的一句话' in E.describe({'kind': 'outside_ghost', 'data': {'fee_line': fee, 'price_yuan': '0.02'}})
    line = E.describe({'kind': 'outside_receipt', 'data': {'ok': False, 'code': 'TOWN_SURVIVAL', 'reason': '她账户余额用完了', 'who': '灯'}})
    assert '（灯）：没成' in line and '（TOWN_SURVIVAL）' in line


def test_status_shows_the_towns_ghost_sentence_verbatim():
    assert "g['line'].strip()" in MAIN and 'ghost_today' in MAIN
