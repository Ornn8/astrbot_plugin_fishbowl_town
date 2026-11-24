"""0.4.14 主聊天里的她和小镇回合对齐（协调 11-24 批；和 DSH 0.9.18 test/align-0918.test.mjs 同一套用例）：
A 打算办到或说办不成为止（最多 6 小时）；B 办不成不换事（只在她手上有打算时）；C 回执照实说；D 主聊天里刚做的钉在回合里（办完 / 过时松开）；
没新事不叫 B：问过她以后那一下、她没定、小镇也没新事，不叫模型；手上压着打算到点自己请小镇叫她一次。"""
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town import isle_tools as IT  # noqa: E402
from astrbot_plugin_fishbowl_town.chat_decide import (NOTE_EXECUTED, NOTE_WAITING, PLAN_MARK, ChatDecide, build_ask,  # noqa: E402
                                                      wish_pull_due)
from astrbot_plugin_fishbowl_town.journal import PIN_HEAD, Journal, cant_why, eta_of, wish_receipt  # noqa: E402
from astrbot_plugin_fishbowl_town.prompts import WISH_TRY, build_turn_prompt  # noqa: E402
from astrbot_plugin_fishbowl_town.runner import Runner  # noqa: E402

T0 = 1_800_000_000.0


def agency(tid, reasons=('idle',)):
    return {'turn_id': tid, 'kind': 'agency', 'deadline': time.time() + 25,
            'packet': {'purpose': 'agency', 'reasons': list(reasons), 'reply': {'format': 'json', 'hint': '{"action":"none"}', 'max_chars': 400},
                       'messages': [{'role': 'system', 'content': '拿主意的规则'}, {'role': 'user', 'content': '码头上有人在泡水；麦香面包房在烤面包。'}]}}


def journal(tmp_path, clock):
    tmp_path.mkdir(parents=True, exist_ok=True)
    return Journal(tmp_path / 'j.json', now=lambda: clock[0])


def test_a_plan_stays_until_done_or_blocked_max_6h(tmp_path):
    clock = [T0]
    j = journal(tmp_path, clock)
    j.wish('坐火车去温泉站', own=True)
    w = j.wish_state()
    assert w['st'] == 'pending'
    j.decision({'action': 'none', 'note': '办不了：岛上没看到火车站'}, wish={'ts': w['ts'], 'verdict': 'blocked', 'why': '岛上没看到火车站'})
    assert j.pending_wish() == '', 'a blocked plan is not 「waiting」 (no execute role)'
    assert j.wish_state()['st'] == 'blocked' and j.wish_state()['why'] == '岛上没看到火车站'
    assert j.decision_lines(1)[0] == '你想「坐火车去温泉站」，没办成：岛上没看到火车站，打算还留着'
    rep = j.report()
    assert '你想「坐火车去温泉站」，小镇回合没办成：岛上没看到火车站。打算还留着' in rep
    # a later turn that only sees it: kept, not its receipt
    clock[0] += 1800
    j.decision({'action': 'none', 'note': '先歇会儿'}, wish={'ts': w['ts'], 'verdict': '', 'why': ''})
    assert j.wish_state()['st'] == 'blocked' and 'wish' not in j.s['journal'][0]
    # the same words again = try again
    j.wish('坐火车去温泉站', own=True)
    assert j.pending_wish() == '坐火车去温泉站'
    j.decision({'action': 'none', 'note': '照你在聊天里定的：坐 22:51 的火车'}, wish={'ts': j.wish_state()['ts'], 'verdict': 'done'})
    assert j.wish_state() is None
    assert j.decision_lines(1)[0].startswith('照你说的：什么都不做，歇着——「坐 22:51 的火车」')
    j.wish('去钓会儿鱼')
    j.decision({'action': 'none', 'note': '钓到一条'}, wish={'ts': j.wish_state()['ts'], 'verdict': 'done'})
    assert j.decision_lines(1)[0].startswith('照主人说的：')
    j.wish('去找阿鲸', own=True)
    j.decision({'action': 'none', 'note': '办不了：阿鲸不在岛上'}, wish={'ts': j.wish_state()['ts'], 'verdict': 'blocked', 'why': '阿鲸不在岛上'})
    clock[0] += 6 * 3600 + 1
    assert j.wish_state() is None
    assert j.decision_lines(1)[0] == '你想「去找阿鲸」，没办成：阿鲸不在岛上', 'no 「打算还留着」 once it is gone'


def test_a_no_verdict_is_as_before_and_a_new_plan_is_not_eaten(tmp_path):
    clock = [T0]
    j = journal(tmp_path, clock)
    j.wish('去码头', own=True)
    j.decision({'action': 'none', 'note': '想歇着'}, wish={'ts': j.wish_state()['ts'], 'verdict': ''})
    assert j.wish_state() is None
    assert j.decision_lines(1)[0].startswith('你在小镇定了：')
    j.wish('去码头', own=True)
    old = j.wish_state()['ts']
    clock[0] += 5
    j.wish('去镜湖', own=True)
    j.decision({'action': 'none'}, wish={'ts': old, 'verdict': 'done'})
    assert j.pending_wish() == '去镜湖'


def test_c_helpers():
    assert wish_receipt({'wish': '坐火车', 'wish_own': True, 'wish_blocked': True, 'why': '没说去哪'}, True) == '你想「坐火车」，没办成：没说去哪，打算还留着'
    assert wish_receipt({'wish': '去钓鱼', 'wish_blocked': True, 'why': '码头关着'}) == '主人想让你「去钓鱼」，没办成：码头关着'
    assert wish_receipt({'action': 'none'}) == ''
    assert cant_why('办不了：码头关着') == '码头关着' and cant_why('办不了 没这个') == '没这个' and cant_why('码头关着') == ''
    assert eta_of('往温泉站去了，1000 米，13.9 分钟到') == 13.9 and eta_of('往X去了：走…共 9 分钟') == 9 and eta_of('买到了') == 0


def test_b_prompts_only_with_a_plan():
    t = agency('p', ['wake'])
    ex = build_turn_prompt(t, role='execute', wish='坐火车', wish_own=True)
    assert '"wish":"blocked"' in ex and '"wish":"done"' in ex
    assert WISH_TRY in build_turn_prompt(agency('p2', ['pulled']), role='pull', wish='去钓鱼', wish_own=False)
    assert WISH_TRY in build_turn_prompt(t, role='wake', wish='去钓鱼', wish_own=False)
    for role in ('wake', 'pull', 'fallback', 'rules', ''):          # condition 1: no plan -> unchanged
        p = build_turn_prompt(t, role=role)
        assert WISH_TRY not in p and '"wish"' not in p, role
        assert p == build_turn_prompt(t, role=role, wish_blocked=False, wish_why='', pins=()), role
    b = build_turn_prompt(t, role='wake', wish='坐火车', wish_own=True, wish_blocked=True, wish_why='没说去哪')
    assert '【你之前定的打算（上次没办成）】' in b and '还是办不了，就照你自己的心意定' in b
    assert WISH_TRY not in b and '【你自己定的打算】' not in b


def test_d_pins_ride_alone_and_are_let_go(tmp_path):
    clock = [T0]
    j = journal(tmp_path, clock)
    src1 = j.event('self', '你在聊天里在岛上走过去：往温泉站去了，1000 米，13.9 分钟到')
    j.pin('走过去：往温泉站去了，1000 米，13.9 分钟到', eta_min=13.9, src=src1)
    src2 = j.event('self', '你在聊天里在岛上买东西：买到了一杯奶茶')
    j.pin('买东西：买到了一杯奶茶', eta_min=0, src=src2)
    pins = j.pins()
    assert len(pins) == 2
    assert not [x for x in j.recent_for_agency() if '你在聊天里在岛上' in x]
    p = build_turn_prompt(agency('d'), pins=[f"- {x['line']}" for x in pins])
    assert PIN_HEAD in p and '往温泉站去了' in p
    j.pins_seen([x['text'] for x in pins])
    assert [x['text'] for x in j.pins()] == ['走过去：往温泉站去了，1000 米，13.9 分钟到']
    clock[0] += 17 * 60 + 1
    assert j.pins() == []
    for x in 'abc':
        j.pin(f'做了{x}', eta_min=500)
    assert [x['text'] for x in j.pins()] == ['做了c', '做了b']
    clock[0] += 2 * 3600 + 1
    assert j.pins() == []


def test_d_the_island_tool_pins_what_was_done(tmp_path):
    j = journal(tmp_path, [T0])

    class Client:
        async def isle_act(self, world, ref):
            return {'ok': True, 'text': '往温泉站去了，1000 米，13.9 分钟到'} if world['do'] == 'walk' else {'ok': False, 'code': 'closed', 'text': '关门了'}
    asyncio.run(IT.act(Client(), {'do': 'walk', 'to': '温泉站'}, journal=j))
    asyncio.run(IT.act(Client(), {'do': 'buy', 'what': '奶茶'}, journal=j))
    pins = j.pins()
    assert len(pins) == 1 and '往温泉站去了' in pins[0]['text']


def decide_world(tmp_path, clock, reply):
    j = journal(tmp_path, clock)

    async def ask_main(text):
        return reply
    d = ChatDecide(j, ask_main=ask_main, home_umo=lambda: 'qq:FriendMessage:1', idle_s=lambda: 600, config={}, now=lambda: clock[0])
    return d, j


class Client:
    def __init__(self):
        self.posted = []

    async def answer(self, tid, body):
        self.posted.append((tid, body))
        return {'ok': True}


def run_turn(j, d, reply, turn, prompts):
    async def ask(prompt):
        prompts.append(prompt)
        return reply
    c = Client()
    r = Runner(c, j, ask, decide=d)
    r.running = True
    asyncio.run(r.handle(turn, time.time()))
    return c


def test_runner_execute_blocked_keeps_the_plan_and_never_sends_the_field(tmp_path):
    d, j = decide_world(tmp_path, [time.time()], '')
    j.wish('坐火车', own=True)
    prompts = []
    c = run_turn(j, d, json.dumps({'action': 'none', 'note': '办不了：没说去哪', 'wish': 'blocked', 'plan': {'hours': 1, 'doing': '等主人说去哪'}}, ensure_ascii=False),
                 agency('ot_1'), prompts)
    assert '照你定的去做' in prompts[0]
    body = json.loads(c.posted[0][1])
    assert 'wish' not in body and body['note'] == '办不了：没说去哪'
    assert j.wish_state()['st'] == 'blocked' and j.wish_state()['why'] == '没说去哪'
    assert j.decision_lines(1)[0] == '你想「坐火车」，没办成：没说去哪，打算还留着'


def test_runner_execute_done(tmp_path):
    d, j = decide_world(tmp_path, [time.time()], '')
    j.wish('去码头泡一会儿', own=True)
    c = run_turn(j, d, json.dumps({'action': 'pier', 'note': '晒太阳', 'wish': 'done', 'plan': {'hours': 1, 'doing': '码头泡着'}}, ensure_ascii=False),
                 agency('ot_2'), [])
    body = json.loads(c.posted[0][1])
    assert 'wish' not in body and body['note'] == f'{NOTE_EXECUTED}：晒太阳'
    assert j.wish_state() is None
    assert j.decision_lines(1)[0].startswith('照你说的：去码头——「晒太阳」')


def test_runner_pins_ride_and_are_let_go(tmp_path):
    d, j = decide_world(tmp_path, [time.time()], '')
    j.pin('买东西：买到了第 1 样', eta_min=0)
    prompts = []
    run_turn(j, d, json.dumps({'action': 'none', 'plan': {'hours': 1, 'doing': '歇着'}}, ensure_ascii=False), agency('ot_3', ['invited']), prompts)
    assert PIN_HEAD in prompts[0] and '买到了第 1 样' in prompts[0]
    assert j.pins() == []


def test_quiet_follow_up(tmp_path):
    clock = [T0]
    d, j = decide_world(tmp_path, clock, '嗯……还没想好')

    async def take(turn):
        r = d.route(turn)
        if d._task:
            await d._task
        return r
    r = asyncio.run(take(agency('a1')))
    assert json.loads(r['answer']) == {'action': 'none', 'next_wake': 8, 'note': '在聊天里想想接下来去哪'}
    assert d.follow_until() > clock[0]
    clock[0] += 8 * 60
    q = d.route(agency('a2', ['wake']))
    assert q.get('quiet') is True and json.loads(q['answer'])['note'] == NOTE_WAITING and json.loads(q['answer'])['next_wake'] == 112
    clock[0] += 30 * 60
    assert d.route(agency('a3', ['wake'])) == {'role': 'wake'}, 'her own time: her model'
    clock[0] += 3 * 3600
    j.s['decide_miss'] = 0
    asyncio.run(take(agency('a4')))
    clock[0] += 8 * 60
    assert d.route(agency('a5', ['wake', 'game_over'])) == {'role': 'wake'}, 'news: her model'
    clock[0] += 3 * 3600
    j.s['decide_miss'] = 0
    asyncio.run(take(agency('a6')))
    j.wish('去钓会儿鱼')
    clock[0] += 8 * 60
    assert d.route(agency('a7', ['wake'])) == {'role': 'wake'}, "the owner's words are waiting: her model"
    assert d.route(agency('a8', ['wake', 'invited'])) == {'role': 'rules'}


def test_quiet_follow_up_after_a_plan(tmp_path):
    clock = [T0]
    d, j = decide_world(tmp_path, clock, f'好\n{PLAN_MARK}去镜湖边走走')

    async def take(turn):
        r = d.route(turn)
        if d._task:
            await d._task
        return r
    asyncio.run(take(agency('b1')))
    clock[0] += 8 * 60
    assert d.route(agency('b2', ['wake'])) == {'role': 'execute'}
    j.decision({'action': 'none'}, wish={'ts': j.wish_state()['ts'], 'verdict': 'done'})
    clock[0] += 40 * 60
    assert d.route(agency('b3', ['wake'])) == {'role': 'wake'}


def test_wish_pull_due():
    w = {'text': '去钓鱼', 'ts': T0, 'st': 'pending'}
    assert wish_pull_due(w, now=T0 + 30) is False
    assert wish_pull_due(w, now=T0 + 300, due=T0 + 600) is False
    assert wish_pull_due(w, now=T0 + 660, due=T0 + 600) is True
    assert wish_pull_due(w, now=T0 + 660, may_pull=False) is False
    assert wish_pull_due({**w, 'st': 'blocked'}, now=T0 + 660) is False
    assert wish_pull_due({**w, 'pulled': T0 + 60}, now=T0 + 660) is False
    assert wish_pull_due({**w, 'pull_after': T0 + 1200}, now=T0 + 660) is False
    assert wish_pull_due({**w, 'pull_tries': 3}, now=T0 + 660) is False
    assert wish_pull_due(None, now=T0) is False


def test_decide_ask_tells_the_blocked_plan():
    assert '你上次定的小镇打算「坐火车」没办成：没说去哪。还想办就把缺的补上、再写一遍' in build_ask(agency('x'), {'text': '坐火车', 'why': '没说去哪'})
    assert '上次定的小镇打算' not in build_ask(agency('y'))
