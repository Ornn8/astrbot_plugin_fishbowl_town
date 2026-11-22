"""0.4.0「主聊天拿主意」（主人 11-18「太割裂了」→「新的作为首选」；和 DSH 0.9.0 test/chat-decide.test.mjs 同一套用例）。"""
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town.chat_decide import (NOTE_ASKING, NOTE_WAITING, PLAN_MARK, ChatDecide, build_ask,  # noqa: E402
                                                      plan_of)
from astrbot_plugin_fishbowl_town.context_note import TownNote  # noqa: E402
from astrbot_plugin_fishbowl_town.journal import Journal  # noqa: E402
from astrbot_plugin_fishbowl_town.prompts import build_turn_prompt  # noqa: E402
from astrbot_plugin_fishbowl_town.runner import Runner  # noqa: E402


def agency(tid, reasons=('idle',)):
    return {'turn_id': tid, 'kind': 'agency', 'deadline': time.time() + 25,
            'packet': {'purpose': 'agency', 'reasons': list(reasons), 'reply': {'format': 'json', 'hint': '{"action":"none"}', 'max_chars': 400},
                       'messages': [{'role': 'system', 'content': '拿主意的规则'}, {'role': 'user', 'content': '码头上有人在泡水；麦香面包房在烤面包。'}]}}


def world(tmp_path, *, idle=600, reply=f'好呀\n{PLAN_MARK}去码头泡一会儿', umo='qq:FriendMessage:1', clock=None):
    clock = clock or [1_800_000_000.0]
    tmp_path.mkdir(parents=True, exist_ok=True)
    j = Journal(tmp_path / 'j.json', now=lambda: clock[0])
    asked = []

    async def ask_main(text):
        asked.append(text)
        return reply() if callable(reply) else reply
    cfg = {}
    idle_box = [idle]
    d = ChatDecide(j, ask_main=ask_main, home_umo=lambda: umo, idle_s=lambda: idle_box[0], config=cfg, now=lambda: clock[0])
    return d, j, asked, cfg, clock, idle_box


def test_plan_line_and_ask_text():
    assert plan_of(f'嗯嗯\n{PLAN_MARK}报名打一局斗地主。') == '报名打一局斗地主'
    assert plan_of('今天不想动') is None
    ask = build_ask(agency('x'))
    assert '插件发的，不是主人说的话' in ask and '码头上有人在泡水' in ask and '【小镇打算】' in ask


def test_routes(tmp_path):
    d, j, *_ = world(tmp_path)
    j.wish('去码头泡一会儿', own=True)
    assert d.route(agency('c1')) == {'role': 'execute'}
    d2, *_ = world(tmp_path / 'b')
    assert d2.route(agency('c2', ['invited'])) == {'role': 'rules'}
    d3, *_ = world(tmp_path / 'c', idle=20)
    assert d3.route(agency('c3', ['pulled', 'table']))['answer']           # the plugin's own start pull is not the town's to decide any more
    d4, _, _, cfg4, *_ = world(tmp_path / 'd')
    cfg4['decide_in_chat'] = False
    assert d4.route(agency('c4')) is None
    d5, *_ = world(tmp_path / 'e', umo='')
    assert d5.route(agency('c5')) is None


def test_away_ask_wait_and_fallback(tmp_path):
    d, j, asked, cfg, clock, _ = world(tmp_path / 'x', reply='嗯')

    async def go():
        a1 = json.loads(d.route(agency('d1'))['answer'])
        assert a1 == {'action': 'none', 'next_wake': 8, 'note': NOTE_ASKING}
        await d._task
        assert j.s['decide_miss'] == 1
        clock[0] += 30 * 60
        w = json.loads(d.route(agency('d2'))['answer'])
        assert w['note'] == NOTE_WAITING and w['next_wake'] == 90
        clock[0] += 91 * 60
        d.route(agency('d3'))
        await d._task
        assert len(asked) == 2 and j.s['decide_miss'] == 2
        assert d.route(agency('d4')) == {'role': 'fallback'}
        d.after('fallback', {'action': 'pier', 'note': '想去晒太阳'})
        assert any('两次没顾上回，这次小镇那边先替你定了：去码头（想去晒太阳）' in e['text'] for e in j.s['journal'])
        assert j.fresh(0) and '先替你定了' in j.fresh(0)[-1]                 # reaches her chat note
    asyncio.run(go())


def test_he_is_talking_rides_on_his_words(tmp_path):
    d, j, asked, cfg, clock, idle = world(tmp_path, idle=20)
    umo = 'qq:FriendMessage:1'
    a = json.loads(d.route(agency('e1'))['answer'])
    assert a['next_wake'] == 5
    assert d.pending_note('other') == ''
    note = d.pending_note(umo)
    assert '【小镇在等你定】' in note and '先好好回他的话' in note and '码头上有人在泡水' in note
    d.note_sent(umo)
    assert d.pending_note(umo) == '' and d.watching(umo)
    assert json.loads(d.route(agency('e2'))['answer'])['note'] == NOTE_ASKING
    d.replied(umo, f'哈哈好呀，晚点聊～\n{PLAN_MARK}去找阿鲸串门')
    assert j.pending_wish() == '去找阿鲸串门' and j.wish_is_own()
    assert not asked, 'no extra call while he is talking'


def test_town_note_appends_the_waiting_ask(tmp_path):
    class D:
        sent = []

        def pending_note(self, key):
            return '' if self.sent else '【小镇在等你定】测试'

        def note_sent(self, key):
            self.sent.append(key)

    async def bonds():
        return {'ok': True}

    async def now_line():
        return ''
    j = Journal(tmp_path / 'j.json')
    note = TownNote(fetch_bonds=bonds, fetch_now=now_line, journal=j, decide=D())
    out = asyncio.run(note.for_turn('k'))
    assert '【小镇在等你定】测试' in out and out.startswith('【鱼缸小镇插件')


def test_prompts():
    ex = build_turn_prompt(agency('p1'), wish='先去码头，晚上八点报名斗地主', wish_own=True, role='execute')
    assert '照你定的去做' in ex and '不要换成别的事' in ex and '办不了：原因' in ex and 'plan.hours' in ex
    ru = build_turn_prompt(agency('p2', ['invited']), role='rules', rules={'text': '朋友约我都去', 'hold': []})
    assert '有件事要马上回' in ru and '只答眼前这件事' in ru and '朋友约我都去' in ru
    plain = build_turn_prompt(agency('p3'))
    assert '该你自己拿主意了' in plain and '照你在聊天里定的' not in plain


def test_runner_executes_her_plan_and_marks_it(tmp_path):
    d, j, *_ = world(tmp_path)
    j.wish('去码头泡一会儿', own=True)

    class Client:
        posted = []

        async def answer(self, tid, body):
            self.posted.append((tid, body))
            return {'ok': True}

    prompts = []

    async def ask(prompt):
        prompts.append(prompt)
        return json.dumps({'action': 'pier', 'note': '晒太阳', 'plan': {'hours': 1, 'doing': '码头泡着'}}, ensure_ascii=False)
    c = Client()
    r = Runner(c, j, ask, decide=d)
    r.running = True
    asyncio.run(r.handle(agency('ot_9'), time.time()))
    assert '照你定的去做' in prompts[0]
    body = json.loads(c.posted[0][1])
    assert body['action'] == 'pier' and body['note'] == '照你在聊天里定的：晒太阳'
    assert j.pending_wish() == ''


def test_runner_answers_the_town_without_calling_the_model_while_waiting(tmp_path):
    d, j, *_ = world(tmp_path, idle=20)

    class Client:
        posted = []

        async def answer(self, tid, body):
            self.posted.append((tid, body))
            return {'ok': True}

    async def ask(prompt):
        raise AssertionError('the town-side ask must not run')
    c = Client()
    r = Runner(c, j, ask, decide=d)
    r.running = True
    asyncio.run(r.handle(agency('ot_1', ['table']), time.time()))       # tables waiting: something to decide
    assert json.loads(c.posted[0][1])['note'] == NOTE_ASKING


def test_history_keeps_no_copyable_plan_line():
    from astrbot_plugin_fishbowl_town.chat_decide import ASK_SHORT, plain_reply
    assert plain_reply(f'还是歇着吧。\n{PLAN_MARK}歇着。') == '还是歇着吧。\n（小镇打算：歇着）'
    assert plan_of(plain_reply(f'好\n{PLAN_MARK}去码头')) is None              # the stored line is never read as a new plan
    assert ASK_SHORT.startswith('【鱼缸小镇插件') and PLAN_MARK not in ASK_SHORT


def test_decided_line_reads_naturally():
    from astrbot_plugin_fishbowl_town.chat_decide import decided_line
    v = decided_line('rules', {'action': 'visit', 'game': 'ddz', 'target': '阿鲸', 'place': '码头', 'note': '说好来的'})
    assert '去码头找阿鲸串门（说好来的）' in v and 'ddz' not in v
    assert '报名一桌（ddz）' in decided_line('fallback', {'action': 'signup', 'game': 'ddz'})


def test_a_tool_step_without_the_line_keeps_waiting(tmp_path):
    d, j, asked, cfg, clock, idle = world(tmp_path, idle=20)
    umo = 'qq:FriendMessage:1'
    d.route(agency('t1'))
    d.note_sent(umo)
    d.replied(umo, '我查一下天气')                           # first LLM response of the turn: a tool call, no plan line
    assert d.watching(umo) and j.s.get('decide_miss', 0) == 0
    d.replied(umo, f'晴天！\n{PLAN_MARK}去码头晒太阳')
    assert j.pending_wish() == '去码头晒太阳'
    assert d.route(agency('t2')) == {'role': 'execute'}
    d2, j2, _, _, clock2, _ = world(tmp_path / 'b', idle=20)
    d2.route(agency('t3'))
    d2.note_sent(umo)
    clock2[0] += 11 * 60
    assert not d2.watching(umo) and j2.s['decide_miss'] == 1   # never wrote the line: one miss after REPLY_WAIT_S


def test_her_reply_to_him_loses_the_plan_line():
    """0.4.1 (owner 11-19 「这玩意在对话里逐条报影响用户体验」): the line is read, then taken out before he sees her reply."""
    from astrbot_plugin_fishbowl_town.chat_decide import strip_plan
    assert strip_plan(f'好呀，我去码头啦~\n{PLAN_MARK}先去码头泡一会儿，晚上八点报名斗地主') == '好呀，我去码头啦~'
    assert strip_plan(f'{PLAN_MARK}歇着') == ''
    assert strip_plan(f'嗯\n（小镇打算：歇着）\n晚安') == '嗯\n晚安'
    assert strip_plan(f'我在呢\n\n{PLAN_MARK}：去电玩城打地鼠。\n\n你吃饭没') == '我在呢\n\n你吃饭没'
    assert strip_plan('平常一句话，说到小镇的打算也不动') == '平常一句话，说到小镇的打算也不动'


def test_the_away_ask_is_her_own_think_and_says_so(tmp_path):
    from astrbot_plugin_fishbowl_town.chat_decide import build_ask
    text = build_ask(agency('t1'))
    assert '不发给他' in text and '一两句说说' not in text
    d, j, asked, cfg, clock, idle = world(tmp_path, idle=10_000)
    asyncio.run(d._ask(agency('t1')))
    assert j.pending_wish() == '去码头泡一会儿' and '你定了小镇的打算' in json.dumps(j.s, ensure_ascii=False)


def test_visit_target_is_a_real_id_or_her_name_never_the_placeholder():
    """11-19 小馋鱼: {"target": "fish_id"} copied from the execute example -> 「目标不对」."""
    from astrbot_plugin_fishbowl_town.prompts import build_turn_prompt
    for role in ('execute', ''):
        text = build_turn_prompt(agency('v1'), wish='去找鱼圆串门', wish_own=True, role=role)
        assert '"target": "fish_id"' not in text
        assert '名单里没有的鱼，直接写她的名字' in text


# --- 0.4.5：小镇到了她自己定的时间来叫她（reasons 里有 'wake'）：一定是她的模型来定 -----------------------------------------------
def test_wake_is_never_skipped_as_idle_or_as_her_rest(tmp_path):
    from astrbot_plugin_fishbowl_town.journal import parse_quiet as pq
    clock = [1_800_000_000.0]
    j = Journal(tmp_path / 'w.json', now=lambda: clock[0])
    j.settle({'action': 'none', 'plan': {'hours': 1, 'doing': '在镜湖边坐坐'}})
    clock[0] += 3600
    assert j.skip({'packet': {'reasons': ['wake']}}) is None
    assert j.skip({'packet': {'reasons': ['wake', 'idle']}}) is None
    assert j.skip({'packet': {'reasons': ['idle']}}), 'idle alone is still skipped'
    j.settle({'action': 'none', 'plan': {'hours': 3, 'doing': '睡个午觉'}})
    clock[0] += 20 * 60
    assert '睡个午觉' in j.skip({'packet': {'reasons': ['idle']}})['why']
    assert j.skip({'packet': {'reasons': ['wake']}}) is None, 'the town woke her at the time it has for her'
    q = pq('00:00-23:59')
    assert '勿扰' in j.skip({'packet': {'reasons': ['wake']}}, quiet=q)['why'], 'quiet hours hold'


def test_wake_inside_the_interval_is_decided_now_by_her_model(tmp_path):
    d, j, asked, cfg, clock, idle = world(tmp_path, reply=f'嗯\n{PLAN_MARK}去镜湖边走走')

    async def go():
        assert json.loads(d.route(agency('w1', ['wake']))['answer'])['note'] == NOTE_ASKING     # long enough since the last ask: as before
        await d._task
        assert d.route(agency('w2', ['wake'])) == {'role': 'execute'}
        j.decision({'action': 'none', 'plan': {'hours': 1, 'doing': '在镜湖边走走'}})
        clock[0] += 3600
        assert json.loads(d.route(agency('w3', ['idle']))['answer'])['note'] == NOTE_WAITING      # a town look of its own still waits
        assert d.route(agency('w4', ['wake'])) == {'role': 'wake'}
        assert d.route(agency('w5', ['wake', 'idle'])) == {'role': 'wake'}
        assert d.route(agency('w6', ['wake', 'invited'])) == {'role': 'rules'}
        assert len(asked) == 1, 'no extra ask inside the interval'
        d2, j2, asked2, _, clock2, _ = world(tmp_path / 'talking', idle=20)                           # he is talking, last ask a minute ago
        j2.s['chat_decide_at'] = clock2[0] - 60
        assert d2.route(agency('w7', ['wake'])) == {'role': 'wake'}
        assert json.loads(d2.route(agency('w7b', ['table']))['answer'])['note'] == NOTE_WAITING
        assert not asked2
        d.after('wake', {'action': 'visit', 'target': '阿鲸', 'place': '面包房'})
        assert any(e['text'] == '你自己定的时间到了，小镇那边照你的打算定了：去面包房找阿鲸串门' for e in j.s['journal'])
    asyncio.run(go())
    p = build_turn_prompt(agency('w8', ['wake']), role='wake')
    assert '该你自己拿主意了 · 你自己定的时间到了' in p and '你上次自己定的再拿主意的时间' in p


def test_pulled_with_her_words_kept_is_decided_now_never_waiting(tmp_path):
    # 0.4.6 (林小鱼 11-22 「她调『去小镇』只回『等它开口』」): 「去小镇」 keeps her words, then pulls -> role pull
    d, j, asked, cfg, clock, idle = world(tmp_path)

    async def start():
        assert d.route(agency('p0', ['pulled', 'table']))['answer']                   # the start pull, nothing kept: as before
        await d._task
    asyncio.run(start())
    d2, j2, asked2, _, _, _ = world(tmp_path / 'kept')
    j2.wish('主人想让你去小镇玩', own=False)
    assert d2.route(agency('p1', ['pulled', 'table'])) == {'role': 'pull'}
    assert d2.route(agency('p2', ['pulled', 'invited'])) == {'role': 'rules'}
    d3, j3, _, _, _, _ = world(tmp_path / 'own')
    j3.wish('去镜湖边走走', own=True)
    assert d3.route(agency('p3', ['pulled'])) == {'role': 'execute'}
    assert not asked2
    d2.after('pull', {'action': 'visit', 'target': '阿鲸', 'place': '面包房'})
    assert any(e['text'] == '你刚才说要去小镇，小镇那边照你的意思定了：去面包房找阿鲸串门' for e in j2.s['journal'])
    p = build_turn_prompt(agency('p4', ['pulled']), role='pull', wish='主人想让你去小镇玩', wish_own=False)
    assert '该你自己拿主意了 · 你刚说要去' in p and '主人想让你去小镇玩' in p


def test_runner_sends_a_wake_turn_to_her_model(tmp_path):
    d, j, *_ = world(tmp_path)
    j.s['chat_decide_at'] = d.now() - 600                   # she was asked 10 minutes ago
    j.settle({'action': 'none', 'next_wake': 30})
    j.s['wake']['at'] = d.now() + 300                       # the plugin's clock says five more minutes: the town's own time still counts

    class Client:
        posted = []

        async def answer(self, tid, body):
            self.posted.append((tid, body))
            return {'ok': True}

    prompts = []

    async def ask(prompt):
        prompts.append(prompt)
        return json.dumps({'action': 'none', 'note': '去镜湖边坐坐', 'plan': {'hours': 2, 'doing': '镜湖边坐坐'}}, ensure_ascii=False)
    c = Client()
    r = Runner(c, j, ask, decide=d)
    r.running = True
    asyncio.run(r.handle(agency('ot_w', ['wake']), time.time()))
    assert len(prompts) == 1 and '你自己定的时间到了' in prompts[0]
    body = json.loads(c.posted[0][1])
    assert body['note'] == '去镜湖边坐坐' and body['next_wake'] == 120
