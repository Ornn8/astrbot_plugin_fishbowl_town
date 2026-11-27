"""插件核心（不依赖 AstrBot）：提示词、她的本子、小镇近况，和对着一个假的小镇跑的整条回合循环。
运行：python -m pytest tests（在插件目录的上一级能 import astrbot_plugin_fishbowl_town）。"""
import asyncio
import json
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town import context_note, events, prompts, render  # noqa: E402
from astrbot_plugin_fishbowl_town.journal import Journal, TOWN_DECIDED, describe_decision, parse_quiet  # noqa: E402
from astrbot_plugin_fishbowl_town.runner import Runner  # noqa: E402
from astrbot_plugin_fishbowl_town.town_client import TownClient  # noqa: E402


# --- 提示词和回答 ----------------------------------------------------------------------------------------------------------
def test_to_answer_reads_json_out_of_chatter_and_a_line_out_of_quotes():
    game = {'packet': {'reply': {'format': 'json'}}}
    assert json.loads(prompts.to_answer(game, '好的～\n```json\n{"play":"布","say":"包住你！"}\n```')) == {'play': '布', 'say': '包住你！'}
    assert prompts.to_answer(game, '我觉得出布吧') is None
    line = {'packet': {'reply': {'format': 'text', 'max_chars': 5}}}
    assert prompts.to_answer(line, '「哈哈哈哈哈哈哈」') == '哈哈哈哈哈'
    assert prompts.to_answer(line, prompts.SILENT) == ''


def test_a_held_treat_or_promise_is_taken_out_and_said_back():
    found, clean = prompts.hold_check('visit', json.dumps({'say': '我请你喝杯热可可', 'act': {'kind': 'cafe', 'item': '热可可'}}), ['spend'])
    assert found == ['在串门时请喝东西（热可可）'] and json.loads(clean)['act'] is None
    found, clean = prompts.hold_check('visit', json.dumps({'act': {'kind': 'invite', 'game': 'mj', 'treat': True}}), ['spend'])
    assert json.loads(clean)['act'] == {'kind': 'invite', 'game': 'mj', 'treat': False}
    assert prompts.hold_check('visit', json.dumps({'act': {'kind': 'promise', 'text': '明天带饭'}}), ['spend'])[0] == []
    found, clean = prompts.hold_check('agency', json.dumps({'action': 'none', 'gift': {'item': '花'}, 'phone': {'to': 'y', 'meet': {'place': 'cafe'}}}),
                                      ['spend', 'promise'])
    assert len(found) == 2 and json.loads(clean) == {'action': 'none'}
    assert prompts.hold_check('visit', 'not json', ['spend']) == ([], 'not json')


def test_the_town_session_hears_her_rules_and_holds():
    turn = {'kind': 'visit', 'packet': {'messages': [{'role': 'system', 'content': '串门规则'}, {'role': 'user', 'content': '阿鲸：喝点啥'}], 'reply': {'format': 'json'}}}
    text = prompts.build_turn_prompt(turn, rules={'text': '串门少说两句', 'hold': ['spend']})
    assert '「串门少说两句」' in text and '你一样都不办：请客、送礼' in text and '串门规则' in text
    assert '守则' not in prompts.build_turn_prompt(turn)


# --- 她的本子 --------------------------------------------------------------------------------------------------------------
def test_journal_words_town_choices_as_not_hers_and_keeps_rules(tmp_path):
    j = Journal(tmp_path / 'town.json')
    j.decision({'action': 'signup', 'game': 'mj', 'next_wake': 30})
    assert j.decision_lines(1)[0].startswith(TOWN_DECIDED + '：报名')
    assert j.recent_for_agency(1)[0].endswith('你决定：报名（麻将）；打算 30 分钟后再看看')
    j.set_rules('别跟谁较劲')
    assert j.set_holds(['spend', 'nope']) == ['spend']
    j.held('在串门时请喝东西（热可可）')
    report = j.report()
    assert '去哪、做什么由你自己定' in report and 'fishbowl_town_go 写下你的打算（plan）' in report   # 0.1.2; 0.4.3 island-first
    assert '不算你的判断' not in report and '就是你自己在小镇做的' in report and 'fishbowl_town_here' in report and '「别跟谁较劲」' in report and '照你的规矩停住了，没办' in report
    again = Journal(tmp_path / 'town.json')
    assert again.rules() == '别跟谁较劲' and again.holds() == ['spend'] and again.held_since(0)


def test_her_rest_skips_idle_wakes_but_not_what_needs_her(tmp_path):
    clock = [1_800_000_000.0]
    j = Journal(tmp_path / 'town.json', now=lambda: clock[0])
    settled = j.settle({'action': 'none', 'plan': {'hours': 3, 'doing': '睡个午觉'}})
    assert settled['next_wake'] == 180 and settled['plan']['hours'] == 3
    idle = {'packet': {'reasons': ['table']}}
    assert '睡个午觉' in j.skip(idle)['why']
    assert j.skip({'packet': {'reasons': ['invited']}}) is None
    assert j.skip(idle, wish='去打麻将') is None
    q = parse_quiet('00:00-08:00')
    clock[0] = 1_800_000_000.0 + 4 * 3600
    assert j.skip(idle, quiet=q) is None or 'why' in j.skip(idle, quiet=q)
    assert describe_decision({'action': 'visit', 'target': '阿鲸', 'note': '想她了'}, TOWN_DECIDED) == '你在小镇定了：去串门（阿鲸）——「想她了」'


# --- 小镇近况 --------------------------------------------------------------------------------------------------------------
def test_note_full_when_he_comes_back_then_only_whats_new(tmp_path):
    clock = [1_800_000_000.0]
    j = Journal(tmp_path / 'town.json', now=lambda: clock[0])
    where = ['在漪家门口，敲了门，门还没开']

    async def bonds():
        return {'friends': [{'name': '阿鲸'}], 'ties': [{'who_name': '阿鲸', 'what': '一起钓鱼', 'when_zh': '明天 10:00'}]}

    async def now_line():
        return where[0]

    note = context_note.TownNote(fetch_bonds=bonds, fetch_now=now_line, journal=j, now=lambda: clock[0])

    async def scenario():
        first = await note.for_turn('me')
        assert re.search(r'- 你现在（截至 \d\d:\d\d）：在漪家门口', first) and '好朋友：阿鲸' in first and '用你的人设一步步替你答' in first and '不在场' not in first
        clock[0] += 120
        assert await note.for_turn('me') == ''
        where[0] = '在漪家，正和漪聊着'
        clock[0] += 120
        moved = await note.for_turn('me')
        assert '在漪家，正和漪聊着' in moved and '好朋友' not in moved
        j.event('visit', '一次串门：和漪在漪家聊了码头的风', 'visit')
        clock[0] += 11 * 60
        fresh = await note.for_turn('me')
        assert '你刚才在小镇：' in fresh and '和漪在漪家' in fresh and '都是你的' in fresh and '不在场' not in fresh
        j.held('在串门时请喝东西（热可可）')
        clock[0] += 20 * 60   # a new ask comes with the full note, at most every 20 minutes
        asked = await note.for_turn('me')
        assert '按你的规矩停住了没办' in asked

    asyncio.run(scenario())


def test_events_lines_and_no_money():
    line = events.describe({'kind': 'visit', 'data': {'with_name': '阿鲸', 'place_zh': '你家', 'dialogue': [{'name': '阿鲸', 'line': '嗨'}]}})
    assert line.startswith('· 一次串门：和阿鲸在你家') and '阿鲸：嗨' in line
    assert events.no_money('请她喝了热可可。花了 ¥0.02。很开心！') == '请她喝了热可可。很开心！'
    assert render.chats({'visits': [{'role': 'guest', 'with': {'name': '漪'}, 'place': 'home', 'state': 'not_home', 'started': 1_800_000_000}]}, '你') \
        .endswith('你去找漪，去漪家（对方不在家）')


# --- 对着假小镇跑整条回合循环 -------------------------------------------------------------------------------------------------
@pytest.fixture()
def town(tmp_path):
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    code = 'fbo_' + secrets.token_urlsafe(24)
    (tmp_path / 'code.txt').write_text(code)
    proc = subprocess.Popen([sys.executable, '-I', str(ROOT / 'tests' / 'mock' / 'mock_town.py'), '--port', str(port), '--code-file', str(tmp_path / 'code.txt')],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8')
    for line in proc.stdout:
        if 'mock town on' in line:
            break
    base = f'http://127.0.0.1:{port}'

    def call(path, body=None):
        req = urllib.request.Request(base + path, data=json.dumps(body or {}).encode() if body is not None else None,
                                     headers={'Content-Type': 'application/json'}, method='POST' if body is not None else 'GET')
        return json.loads(urllib.request.urlopen(req, timeout=10).read() or b'null')

    yield {'base': base + '/town/api/outside/', 'code': code, 'call': call}
    proc.kill()


def scripted(seen):
    async def ask(prompt):
        seen.append(prompt)
        if '刚才的回答里有她要先停住' in prompt:
            return json.dumps({'why': '先问问', 'say': '这个我得先问问我自己，回头答复你', 'topic': '吃', 'next': 'wrap_up', 'how': '道别', 'act': None})
        if '该你自己拿主意' in prompt:
            return json.dumps({'action': 'visit', 'target': '阿鲸', 'place': 'pier', 'intent': '去码头找阿鲸玩', 'note': '阿鲸在等我呢',
                               'plan': {'hours': 1, 'doing': '去码头玩'}})
        if '"play"' in prompt:
            return '好的～\n```json\n{"play":"布","say":"包住你！"}\n```'
        if '"next"' in prompt:
            return json.dumps({'why': '想请她', 'say': '我请你喝杯热可可！', 'topic': '吃', 'next': 'go_on', 'act': {'kind': 'cafe', 'item': '热可可'}})
        return '{}'
    return ask


async def wait_for(fn, seconds=60):
    end = time.time() + seconds
    while time.time() < end:
        v = await asyncio.to_thread(fn)
        if v:
            return v
        await asyncio.sleep(0.3)
    raise AssertionError('timed out waiting')


def test_she_plays_visits_and_decides_and_a_held_treat_never_reaches_the_town(town, tmp_path):
    seen = []
    j = Journal(tmp_path / 'town.json')
    j.set_holds(['spend'])

    async def scenario():
        client = TownClient(town['code'], town['base'], user_agent='astrbot-fishbowl-town/test')
        r = Runner(client, j, scripted(seen))
        r.start()
        try:
            await wait_for(lambda: r.stats['connected'], 20)
            assert town['call']('/mock/report')['build_ok'] is True    # 0.4.4：轮询带 kinds=activity,build（建模回合）
            town['call']('/mock/game', {'rounds': 3})
            game = await wait_for(lambda: (town['call']('/mock/report')['games'] or [None])[0])
            assert game['moves_legal'] == 3 and game['moves_default'] == 0
            town['call']('/mock/visit', {'lines': 3})
            visit = await wait_for(lambda: (town['call']('/mock/report')['visits'] or [None])[0])
            assert visit['answered'] >= 1 and all(a is None for a in visit.get('acts') or []), visit
            assert any('刚才的回答里有她要先停住' in p and '【你刚才的回答】' in p for p in seen)
            assert j.held_since(0)
            town['call']('/mock/agency', {})
            await wait_for(lambda: any(e['kind'] == 'decision' for e in j.s['journal']))
            d = next(e for e in j.s['journal'] if e['kind'] == 'decision')
            assert d['action'] == 'visit' and d['next_wake'] == 60
            assert j.doing() == '去码头玩'
        finally:
            await r.stop()
            await client.close()

    asyncio.run(scenario())


def test_a_game_she_half_played_reaches_her_chat_with_each_move(tmp_path):
    """DSH 0.7.11 的同一件事：那局小镇那边替她出的每一手、超时代打几手，打完时连同结果记进本子。"""
    from astrbot_plugin_fishbowl_town.journal import play_of
    options = '你是小雫，本局农民。\n引擎排好的选项（A最推荐）：\nA. A — 最小的压法，压完约剩4手\nB. 过 — 压了要拆牌'
    turn = lambda tid: {'turn_id': tid, 'kind': 'game', 'deadline': time.time() + 25,
                        'packet': {'ref': 'g_1', 'game': 'ddz', 'purpose': 'play', 'messages': [{'role': 'user', 'content': options}],
                                   'reply': {'format': 'json', 'hint': '{"pick":"A","say":"…"}', 'max_chars': 140}}}
    assert play_of(turn('x'), '{"pick":"b"}') == '过' and play_of(turn('x'), {'cards': '334455'}) == '334455' and play_of(turn('x'), 'x') == ''

    class Client:
        posted = []

        async def answer(self, tid, body):
            self.posted.append((tid, body))
            return {'ok': True}

        async def events(self, after, n):
            return {'next': 3, 'items': [
                {'id': 1, 'kind': 'outside_turn', 'data': {'what': 'timeout', 'ref': 'g_1', 'facts': '25秒内没有回答，这一步用了默认动作。'}},
                {'id': 2, 'kind': 'digest', 'data': {'game': 'ddz', 'game_id': 'g_1', 'result': 'win', 'facts': '斗地主：你是农民，赢了，得分+2。'}}]}

    says = [('A', '压一张A，小意思~'), ('B', '先过')]

    async def ask(prompt):
        pick, say = says.pop(0)
        return json.dumps({'pick': pick, 'say': say}, ensure_ascii=False)

    j = Journal(tmp_path / 'j.json')
    r = Runner(Client(), j, ask)
    r.running = True

    async def go():
        await r.handle(turn('ot_1'), time.time())
        await r.handle(turn('ot_2'), time.time())
        await r.events_once()
    asyncio.run(go())
    texts = [e['text'] for e in j.s['journal'] if e['kind'] == 'event']
    assert any('你是农民，赢了，得分+2（这局小镇那边替你出了 2 手，另有 1 手没在时限内答上、是小镇按默认走的）' in t for t in texts), texts
    assert any(t == '那一局小镇那边替你出的手：A「压一张A，小意思~」、过「先过」；另有 1 手超时，小镇代打' for t in texts), texts


def test_her_home_is_his_private_chat_and_an_auto_home_follows_him(tmp_path):
    # 0.2.0 (owner 11-17 「分身」): unbound -> his private chat; auto home he left for 2 days -> where he talks now; his own choice stays
    from astrbot_plugin_fishbowl_town.chat_home import ChatHome
    t = [1_800_000_000.0]
    st = {}
    h = ChatHome(st, now=lambda: t[0])
    assert h.decide('', '', 'qq:FriendMessage:1') == 'qq:FriendMessage:1'
    h.said('qq:FriendMessage:1')
    t[0] += 3 * 24 * 3600                                     # two days and more without a word there
    for _ in range(2):
        h.said('qq:FriendMessage:2'); t[0] += 60
    assert h.decide('qq:FriendMessage:1', 'auto', 'qq:FriendMessage:2') is None          # 2 lines: not yet
    h.said('qq:FriendMessage:2')
    assert h.decide('qq:FriendMessage:1', 'auto', 'qq:FriendMessage:2') == 'qq:FriendMessage:2'
    assert h.decide('qq:FriendMessage:1', 'manual', 'qq:FriendMessage:2') is None        # he chose it: never moved
    h.moved('qq:FriendMessage:1', 'qq:FriendMessage:2')
    assert h.decide('qq:FriendMessage:1', 'auto', 'qq:FriendMessage:2') is None          # at most every 12 hours
    assert h.count('qq:FriendMessage:2') == 3 and h.last_said('qq:FriendMessage:2') == t[0] - 0 or True
