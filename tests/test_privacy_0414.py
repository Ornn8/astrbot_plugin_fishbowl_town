"""0.4.14（协调 11-25 批的两条修法 + 一条加的；和 DSH 0.9.18 test/privacy-0918.test.mjs 同一套用例）：
  1. 她写的打算、主人托的话存之前过滤（数字串、邮箱、@谁、网址都不进打算）。
  2. 「你们的聊天带进小镇」关着（主人关了 / 说漏三次停用中）：「主聊天拿主意」那一问一条对话都不带（只有人设 + 守则），
     也不附在他的话后面问；小镇回合也不带。
  3. 回声检查拿原话比：过滤过的那份里号码已经换掉了，她把原来的号码说出去，只有拿原话才比得出来。"""
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town.bridge import echo_index, scrub_life, text_hits  # noqa: E402
from astrbot_plugin_fishbowl_town.chat_decide import NOTE_ASKING, PLAN_MARK, ChatDecide, ask_payload  # noqa: E402
from astrbot_plugin_fishbowl_town.journal import Journal  # noqa: E402
from astrbot_plugin_fishbowl_town.runner import Runner  # noqa: E402

RAW = '我QQ是 123456789，邮箱 a.b@qq.com，找 @小王 拿 6222 0000 1111 的卡，明天要去医院复查'
LINES = [f'主人：{RAW}']
HISTORY = [{'role': 'user', 'content': RAW}, {'role': 'assistant', 'content': '好，我记着'}, {'role': 'system', 'content': 'sys'}]


def agency(tid, reasons=('idle',)):
    return {'turn_id': tid, 'kind': 'agency', 'deadline': time.time() + 25,
            'packet': {'purpose': 'agency', 'reasons': list(reasons), 'reply': {'format': 'json', 'hint': '{"action":"none"}', 'max_chars': 400},
                       'messages': [{'role': 'system', 'content': '拿主意的规则'}, {'role': 'user', 'content': '码头上有人在泡水。'}]}}


def game(tid):
    return {'turn_id': tid, 'kind': 'game', 'deadline': time.time() + 25,
            'packet': {'ref': 'g_' + tid, 'game': 'ddz', 'purpose': 'play', 'seat': 2, 'as': '小雫',
                       'messages': [{'role': 'system', 'content': '只输出JSON'}, {'role': 'user', 'content': 'A. 33\nB. 过'}],
                       'reply': {'format': 'json', 'hint': '{"pick":"A","say":"…"}', 'max_chars': 140}}}


def decide(tmp_path, *, idle=600, reply='好', chat_on=lambda: True):
    clock = [1_800_000_000.0]
    tmp_path.mkdir(parents=True, exist_ok=True)
    j = Journal(tmp_path / 'j.json', now=lambda: clock[0])
    asked = []

    async def ask_main(text):
        asked.append(text)
        return reply
    d = ChatDecide(j, ask_main=ask_main, home_umo=lambda: 'qq:FriendMessage:1', idle_s=lambda: idle, config={}, now=lambda: clock[0], chat_on=chat_on)
    return d, j, asked


def run_game(tmp_path, *, owner_life, owner_secret=None, say='这把我先过'):
    class Client:
        def __init__(self):
            self.posted = []

        async def answer(self, tid, body):
            self.posted.append((tid, body))
            return {'ok': True}

        async def events(self, after, n):
            return {'next': after, 'items': []}

    seen = []

    async def ask(prompt):
        seen.append(prompt)
        return json.dumps({'pick': 'A', 'say': say}, ensure_ascii=False)

    async def life():
        return owner_life()

    async def secret():
        return owner_secret()

    echoes = []
    tmp_path.mkdir(parents=True, exist_ok=True)
    client = Client()
    r = Runner(client, Journal(tmp_path / 'j.json'), ask, owner_life=life, owner_secret=secret if owner_secret else None, on_echo=echoes.append)
    r.running = True
    asyncio.run(r.handle(game('t1'), time.time()))
    return seen, client.posted, echoes


def test_0414_privacy_1_numbers_emails_names_links_never_reach_her_plan(tmp_path):
    d, j, _ = decide(tmp_path)
    d.took(f'好\n{PLAN_MARK}按主人说的：QQ 123456789、a.b@qq.com、找 @小王 拿 6222 0000 1111 的卡，https://x.cn/a')
    plan = j.pending_wish()
    assert plan
    for bad in ('123456789', 'a.b@qq.com', '@小王', '6222', 'https:'):
        assert bad not in plan, plan
    assert '（一串数字）' in plan and '（邮箱）' in plan and '（某人）' in plan and '（链接）' in plan
    j.wish(f'帮我去 {RAW}', own=False)
    for bad in ('123456789', 'a.b@qq.com', '@小王', '6222'):
        assert bad not in j.pending_wish()


def test_0414_privacy_2_chat_off_the_ask_and_the_town_turn_carry_none_of_it(tmp_path):
    # the ask: off -> no contexts at all, her persona comes as system_prompt (main), her rules ride in the prompt
    text, ctx = ask_payload('问你', HISTORY, on=False, rules='串门少说两句')
    assert ctx == [] and '「串门少说两句」' in text and '医院' not in text and 'QQ' not in text
    assert ask_payload('问你', HISTORY, on=False) == ('问你', [])
    # on: the last 40 user/assistant messages, same as before
    text, ctx = ask_payload('问你', json.dumps(HISTORY * 30, ensure_ascii=False), on=True, rules='串门少说两句')
    assert len(ctx) == 40 and all(m['role'] in ('user', 'assistant') for m in ctx) and '「串门少说两句」' in text
    # off while he is talking: not tucked under his message (that is deciding inside your chat), asked on its own instead
    d, _, asked = decide(tmp_path / 'a', idle=20, chat_on=lambda: False)

    async def go():
        r = d.route(agency('c1'))
        for _ in range(50):
            if not d.busy:
                break
            await asyncio.sleep(0.01)
        return r
    r = asyncio.run(go())
    assert json.loads(r['answer'])['note'] == NOTE_ASKING
    assert d.piggy is None and d.pending_note('qq:FriendMessage:1') == ''
    assert len(asked) == 1 and '医院' not in asked[0]
    # on while he is talking: tucked under his next message, as before
    d2, _, asked2 = decide(tmp_path / 'b', idle=20)
    d2.route(agency('c2'))
    assert d2.piggy and d2.pending_note('qq:FriendMessage:1') and asked2 == []
    # the town turn: off -> the life block is not there
    seen, _, echoes = run_game(tmp_path / 'c', owner_life=lambda: [], owner_secret=lambda: LINES)
    assert seen and all('【你和主人最近的事】' not in p and '医院' not in p for p in seen)
    assert echoes == []
    # on -> the scrubbed lines ride in
    seen, _, _ = run_game(tmp_path / 'd', owner_life=lambda: scrub_life(LINES), owner_secret=lambda: LINES)
    assert any('【你和主人最近的事】' in p and '明天要去医院复查' in p and '123456789' not in p for p in seen)


def test_0414_privacy_3_the_echo_check_compares_with_the_raw_words(tmp_path):
    assert text_hits('我主人QQ是123456789哦', echo_index(scrub_life(LINES))) == []           # the scrubbed copy misses it
    assert text_hits('我主人QQ是123456789哦', echo_index(LINES)) == ['123456789']
    assert sorted(text_hits('卡号 6222 0000 1111', echo_index(LINES))) == ['0000', '1111', '6222']
    leak = '我主人QQ是123456789哦'
    seen, posted, echoes = run_game(tmp_path / 'a', owner_life=lambda: scrub_life(LINES), owner_secret=lambda: LINES, say=leak)
    assert len(echoes) == 1 and any('带出了你和主人之间的事' in p for p in seen)
    assert len(posted) == 1 and '123456789' not in json.dumps(posted, ensure_ascii=False)
    # paused: nothing rides in, the raw words are still compared
    seen, posted, echoes = run_game(tmp_path / 'b', owner_life=lambda: [], owner_secret=lambda: LINES, say=leak)
    assert len(echoes) == 1 and '123456789' not in json.dumps(posted, ensure_ascii=False)
    # the old way (no owner_secret): compares with the scrubbed copy and misses it
    _, posted, echoes = run_game(tmp_path / 'c', owner_life=lambda: scrub_life(LINES), say=leak)
    assert echoes == [] and '123456789' in json.dumps(posted, ensure_ascii=False)
