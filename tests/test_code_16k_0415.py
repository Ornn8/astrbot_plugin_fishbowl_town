"""0.4.15 程序放宽到 16k（小镇 fix/1126-code-16k；和 DSH 0.9.19 test/code-16k-0919.test.mjs 同一套用例）：
查了写程序那一页的回合，递回那一页时说整段 24000 字、程序 16000 字；平常回合的「不超过 N 字」不动；
交之前插件量一下，超了把小镇会说的那句给她、请她删短再交。"""
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town import isle as I  # noqa: E402
from astrbot_plugin_fishbowl_town.journal import Journal  # noqa: E402
from astrbot_plugin_fishbowl_town.runner import Runner  # noqa: E402

V1 = 'a1b2c3d4e5f6'
BLOCKS = {'rules': '岛上只用鲸币。', 'verbs': ['open 开店'], 'goods': '粮 鱼', 'law': '不许场外买卖。'}
LOOK_CODE = '{"action":"none","world":{"do":"look","topic":"怎么做","verb":"写程序"}}'


def turn_of(code=16000):
    reply = {'format': 'json', 'hint': '{"action":"none"}', 'max_chars': 400, **({'code_max_chars': code} if code else {})}
    return {'turn_id': 'ot_code', 'kind': 'agency', 'deadline': time.time() + 120,
            'packet': {'purpose': 'agency', 'reasons': ['pulled'], 'isle': {'static': V1, 'within_s': 120},
                       'messages': [{'role': 'system', 'content': '拿主意的规则'}, {'role': 'user', 'content': '{"我":{"鲸币":800}}'}],
                       'reply': reply}}


def program(n):
    line = 'api.say("讲到这里");\n'
    return (line * (n // len(line) + 1))[:n]


def open_with(code):
    return json.dumps({'action': 'none', 'world': {'do': 'open', 'lot': 'L-12', 'mode': 'own', 'code': code}}, ensure_ascii=False)


class Town:
    def __init__(self):
        self.posted, self.reads = [], []

    async def answer(self, tid, body):
        self.posted.append((tid, body))
        return {'ok': True}

    async def isle_static(self, have):
        return {'version': V1, 'blocks': BLOCKS}

    async def isle_read(self, tid, read):
        self.reads.append((tid, read))
        return {'ok': True, 'text': '写程序的接口：export function visit(api, guest) {…}' if read.get('topic') == '怎么做' else f"1. {read['topic']}", 'next': None}


def run_turn(tmp_path, answers, turn=None):
    t = Town()
    prompts = []

    async def ask(prompt):
        prompts.append(prompt)
        return answers[min(len(prompts) - 1, len(answers) - 1)]
    r = Runner(t, Journal(tmp_path / 'j.json'), ask)
    r.running = True
    asyncio.run(r.handle(turn or turn_of(), time.time()))
    return t, prompts


def test_helpers():
    assert I.code_limit_of(turn_of()) == {'code': 16000, 'total': I.CODE_ANSWER_MAX}
    assert I.code_limit_of(turn_of(0)) is None and I.code_limit_of({}) is None
    for verb in ('写程序', 'sdk', 'code', '开店 自己写程序'):
        assert I.is_code_read({'do': 'look', 'topic': '怎么做', 'verb': verb}), verb
    for r in ({'topic': '怎么做', 'verb': '建模'}, {'topic': '怎么做', 'verb': '开店'}, {'topic': '地'}, {'topic': '店', 'verb': '写程序'}, None):
        assert not I.is_code_read(r), r
    lim = I.code_limit_of(turn_of())
    assert I.code_too_long(open_with(program(16000)), lim) == ''
    assert I.code_too_long(open_with(program(16001)), lim) == '程序太长了：现在 16001 字，最多 16000 字'
    deep = json.dumps({'world': {'do': 'door', 'steps': [{'rules': program(16500)}]}}, ensure_ascii=False)
    assert I.code_too_long(deep, lim) == '程序太长了：现在 16500 字，最多 16000 字'
    long = json.dumps({'action': 'none', 'note': '字' * 24001}, ensure_ascii=False)
    n = len(json.dumps(json.loads(long), ensure_ascii=False, separators=(',', ':')))
    assert I.code_too_long(long, lim) == f'回答太长了：现在 {n} 字，最多 24000 字'
    spaced = json.dumps({'action': 'none', 'note': '字' * 23970}, ensure_ascii=False, indent=4)
    assert len(spaced) > 24000 - 30 and I.code_too_long(spaced, lim) == '', 'counted without blanks, like the town'
    assert I.code_too_long('not json', lim) == '' and I.code_too_long(open_with(program(20000)), None) == ''
    assert I.build_too_long_followup('程序太长了：现在 16001 字，最多 16000 字').startswith('这份回答交不了：程序太长了：现在 16001 字，最多 16000 字。')
    f = I.build_read_followup({'topic': '怎么做', 'verb': '写程序'}, '页', 2, lim)
    assert '【这一回合交程序】整段回答最多 24000 字、程序本身（code / rules）最多 16000 字，不受开头「不超过 N 字」的限制' in f
    assert '这一回合交程序' not in I.build_read_followup({'topic': '怎么做', 'verb': '写程序'}, '页', 2)


def test_program_page_brings_24000_and_a_15000_char_program_goes_whole(tmp_path):
    code = program(15000)
    t, prompts = run_turn(tmp_path, [LOOK_CODE, open_with(code)])
    assert '只输出一段 JSON（不超过 400 字）' in prompts[0]
    assert '【这一回合交程序】整段回答最多 24000 字、程序本身（code / rules）最多 16000 字' in prompts[1]
    assert len(t.posted) == 1 and len(prompts) == 2
    got = json.loads(t.posted[0][1])['world']['code']
    assert got == code and len(got) == 15000 and '\n' in got


def test_a_normal_look_has_no_program_line(tmp_path):
    t, prompts = run_turn(tmp_path, ['{"action":"none","world":{"do":"look","topic":"地"}}', '{"action":"none"}'])
    assert '这一回合交程序' not in prompts[1] and '不超过 400 字' in prompts[0]


def test_a_later_page_still_carries_the_line(tmp_path):
    t, prompts = run_turn(tmp_path, [LOOK_CODE, '{"action":"none","world":{"do":"look","topic":"地"}}', '{"action":"none"}'])
    last = prompts[2].rsplit('【你刚查的：', 1)[1]
    assert last.startswith('挂着的地】') and '【这一回合交程序】' in last


def test_old_town_without_code_max_chars_adds_nothing(tmp_path):
    t, prompts = run_turn(tmp_path, [LOOK_CODE, '{"action":"none"}'], turn=turn_of(0))
    assert '这一回合交程序' not in prompts[1]


def test_over_16000_she_gets_the_towns_sentence_once_and_the_shorter_one_goes(tmp_path):
    short = program(9000)
    t, prompts = run_turn(tmp_path, [LOOK_CODE, open_with(program(16500)), open_with(short)])
    assert len(prompts) == 3
    assert prompts[2].rstrip().endswith('照原来的要求只输出那段 JSON 再交一次。')
    assert '这份回答交不了：程序太长了：现在 16500 字，最多 16000 字。' in prompts[2]
    assert json.loads(t.posted[0][1])['world']['code'] == short


def test_still_too_long_goes_as_it_is_after_one_ask(tmp_path):
    t, prompts = run_turn(tmp_path, [open_with(program(16500))])
    assert len(prompts) == 2 and len(json.loads(t.posted[0][1])['world']['code']) == 16500
