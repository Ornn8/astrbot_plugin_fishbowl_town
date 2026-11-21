"""0.4.3 她住在鱼缸岛上（主人 11-21）：给她的话以岛为先、路要自己走、老镇 11-28 关门；老镇的接口关了不重试。"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town import old_town as O  # noqa: E402
from astrbot_plugin_fishbowl_town.chat_decide import build_ask, build_note_ask  # noqa: E402
from astrbot_plugin_fishbowl_town.context_note import render_block  # noqa: E402
from astrbot_plugin_fishbowl_town.journal import TOWN_WHO  # noqa: E402
from astrbot_plugin_fishbowl_town.prompts import build_turn_prompt  # noqa: E402
from astrbot_plugin_fishbowl_town.town_client import TownClient, TownError, transient  # noqa: E402

BEFORE, AFTER = O.CLOSE_AT - 3600, O.CLOSE_AT + 60
OLD_WORDS = ('去码头', '报名打一局', '想在镇上做什么', '去不去打牌')


def agency(isle=True, actions=('none', 'signup', 'visit', 'pier')):
    p = {'purpose': 'agency', 'reasons': ['idle'], 'actions': list(actions),
         'messages': [{'role': 'system', 'content': '拿主意的规则'}, {'role': 'user', 'content': '{"我":{"鲸币":800}}'}],
         'reply': {'format': 'json', 'hint': '{"action":"none"}', 'max_chars': 400}}
    if isle:
        p['isle'] = {'static': 'a1b2c3d4e5f6', 'within_s': 120}
    return {'turn_id': 't1', 'kind': 'agency', 'packet': p}


def test_isle_lines_follow_the_turn_and_the_date():
    assert O.isle_lines(agency(), BEFORE) == [O.ISLE_HOME, O.OLD_TOWN]
    assert O.isle_lines(agency(), AFTER) == [O.ISLE_HOME]                         # 11-28 起不再提老镇
    assert O.isle_lines(agency(isle=False), BEFORE) == [O.OLD_TOWN]               # 不在岛上：不说她住在岛上
    assert O.isle_lines(agency(actions=('none',)), BEFORE) == [O.ISLE_HOME]       # 小镇不列老镇的动作了：不提
    assert '¥' not in O.ISLE_HOME + O.OLD_TOWN and '饭票' not in O.ISLE_HOME + O.OLD_TOWN


def test_agency_prompt_is_island_first_and_short(monkeypatch):
    monkeypatch.setattr(O, 'CLOSE_AT', 4_000_000_000)                             # 老镇还开着（测试不跟着日子变）
    text = build_turn_prompt(agency(), seconds_left=100)
    assert O.ISLE_HOME in text and '去哪、做什么都照你自己的性子' in text
    assert '去不去打牌、找谁串门' not in text and '小镇不替你安排' not in text
    ex = build_turn_prompt(agency(), wish='先去镜湖边走走', wish_own=True, role='execute')
    after = build_turn_prompt(agency(actions=('none',)), wish='先去镜湖边走走', wish_own=True, role='execute')
    assert '老镇的报名、串门、码头也可以写进 plan.steps' in ex and 'plan.steps' not in after and '还没走到' in after
    assert '{steps}' not in ex + after
    monkeypatch.setattr(O, 'CLOSE_AT', 0)                                         # 11-28 以后：老镇的事一句不提
    closed_ex = build_turn_prompt(agency(), wish='先去镜湖边走走', wish_own=True, role='execute')
    assert 'plan.steps' not in closed_ex and O.OLD_TOWN not in closed_ex and O.ISLE_HOME in closed_ex


def test_her_own_ask_has_no_old_town_examples_or_prices():
    ask = build_ask(agency())
    assert not any(w in ask for w in OLD_WORDS), ask
    assert O.ISLE_HOME in ask and '按小镇的价钱' not in ask and '镜湖' in ask
    assert not any(w in build_note_ask('岛上的情况') for w in OLD_WORDS)
    assert '去码头' not in TOWN_WHO and '岛上' in TOWN_WHO


def test_owner_note_names_the_old_town_close_only_before_it():
    bonds = {'friends': [{'name': '阿鲸'}]}
    assert O.OLD_TOWN in render_block(bonds, at=BEFORE)
    assert O.OLD_TOWN not in render_block(bonds, at=AFTER)
    assert render_block({}, at=BEFORE) == ''                                      # 没别的事：这一行自己不成一段


def test_status_lines_use_the_towns_isle_field():
    assert O.now_with_isle({'now_line': '在家歇着，没在镇上哪个地方', 'isle': {'open': True, 'here': True}}) == '在鱼缸岛上'
    assert O.now_with_isle({'now_line': '在桌游馆打麻将，正在打', 'isle': {'open': True, 'here': True}}) == '在桌游馆打麻将，正在打'
    assert O.now_with_isle({'now_line': '在家歇着，没在镇上哪个地方', 'isle': {'open': True, 'here': None}}) == '在家歇着，没在镇上哪个地方'
    assert O.isle_status_line({'isle': {'open': True, 'here': True}}) == '鱼缸岛：她在岛上'
    assert O.isle_status_line({'isle': {'open': False, 'here': False}}).endswith('没对她开放')
    assert O.isle_status_line({}) == '' and O.isle_status_line(None) == ''


def test_closed_text_before_and_after_the_close():
    e = TownError('TOWN_OFF', 404, '串门现在没开放。')
    assert O.closed_text(e, now=BEFORE) == '这一项这会儿没开放：串门现在没开放。'
    her, owner = O.closed_text(e, now=AFTER), O.closed_text(e, owner=True, now=AFTER)
    assert '11 月 28 日关门' in her and '鱼缸岛' in her and '去小镇' in her
    assert '你不能' not in owner and '由她自己定' in owner and '跟她说' in owner


def test_a_closed_old_town_route_is_not_asked_again():
    c = TownClient('fbo_testcode123')
    calls = []

    async def fake(method, path, **kw):
        calls.append(path)
        if path.startswith('visits'):
            raise TownError('TOWN_OFF', 404)
        if path == 'album':
            raise TownError('TOWN_NOT_FOUND', 404)          # 「没有这个」不是关门：照旧每次问
        if path == 'turns':
            raise TownError('TOWN_OFF', 404)
        return {'ok': True}
    c._request = fake

    async def run():
        for _ in range(3):
            for call in (c.visit_list, c.album, c.turns):
                try:
                    await call()
                except TownError:
                    pass
        try:
            await c.visit_lines('v1')                         # 同一块（visits/…）也不再问
        except TownError as error:
            assert error.code == 'TOWN_OFF' and error.status == 404
    asyncio.run(run())
    assert calls.count('visits') == 1 and calls.count('album') == 3 and calls.count('turns') == 3
    assert 'visits/v1' not in calls

    async def restart(method, path, **kw):                 # 小镇重启（回合那条路回 503）：记着的「关了」作废
        calls.append(path)
        raise TownError('TOWN_RESTARTING', 503)
    c._request = restart

    async def again():
        try:
            await c.turns()
        except TownError:
            pass
        c._request = fake
        try:
            await c.visit_list()
        except TownError:
            pass
    asyncio.run(again())
    assert calls.count('visits') == 2
    assert transient('TOWN_OFF', 'turns') and transient('TOWN_OFF') and not transient('TOWN_OFF', 'visits')
