"""0.4.4 建模回合（和 DSH 0.9.7 test/build.test.mjs 同一套用例；主人 11-21「建模回合加上吧，插件说明里面记得弄」）：
轮询写 kinds=activity,build；建模回合用回合自己的时限（120 秒）、最多 8000 字；她的模型没答上就交 {"skip":true}，不替她建模。"""
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town import build as B  # noqa: E402
from astrbot_plugin_fishbowl_town import isle as I  # noqa: E402
from astrbot_plugin_fishbowl_town.guide import BUILD_STEP, guide_text  # noqa: E402
from astrbot_plugin_fishbowl_town.journal import Journal  # noqa: E402
from astrbot_plugin_fishbowl_town.prompts import ISLE_BUILD_NOTE, build_turn_prompt  # noqa: E402
from astrbot_plugin_fishbowl_town.runner import Runner  # noqa: E402
from astrbot_plugin_fishbowl_town.town_client import TURN_KINDS, TownClient  # noqa: E402

# 小镇建模说明里查的那串词（town worlds/isle/build_guide.py STYLE_WORDS）：插件给她的话里一个也不能有
STYLE_WORDS = ('风格', '低多边形', '低模', 'low-poly', 'lowpoly', 'low poly', '可爱', '温馨', '好看', '漂亮', '精致', '美观', '配色', '色调', '氛围', '童话', '卡通',
               '治愈', '柔和', '暖色', '冷色', '复古', '简约', '写实', '梦幻', 'cute', 'cozy', 'style', 'aesthetic', '和岛上', '统一的')
GUIDE = '建模说明（只讲怎么交、怎么量、怎么收钱；长什么样你自己定）\n【你的地】L-0107：x 方向宽 12 米，z 方向深 10 米'
IDEA = '地：L-0107\n你的想法：两层的家，带个阳台'
MODEL = json.dumps({'kind': 'shapes', 'shapes': {'shapes': [{'type': 'box', 'pos': [0, 0, 0], 'size': [6, 3, 4], 'material': 'plaster'},
                                                           {'type': 'gable', 'pos': [0, 3, 0], 'size': [6, 1.5, 4], 'material': 'tile'}]}},
                   ensure_ascii=False, separators=(',', ':'))


def build_turn(tid='ot_build', within=120, deadline=True, max_chars=8000):
    return {'turn_id': tid, 'kind': 'build', **({'deadline': time.time() + within} if deadline else {}),
            'packet': {'purpose': 'isle_build', 'game': 'build', 'lot': 'L-0107', 'as': '小汤圆', 'ref': 'b_1', **({'isle': {'within_s': within}} if within else {}),
                       'messages': [{'role': 'system', 'content': GUIDE}, {'role': 'user', 'content': IDEA}],
                       'reply': {'format': 'json', **({'max_chars': max_chars} if max_chars else {})}}}


def test_the_poll_says_the_program_answers_activity_and_build_turns():
    assert TURN_KINDS == 'activity,build'
    c = TownClient('fbo_test')
    seen = []

    async def request(method, path, *, query=None, body=None, timeout=15):
        seen.append((method, path, query, timeout))
        return {'turns': []}
    c.request = request
    asyncio.run(c.turns(25))
    assert seen == [('GET', 'turns', {'wait': 25, 'kinds': 'activity,build'}, 35)]


def test_helpers_time_limit_characters_budget_prompt():
    assert B.is_build(build_turn()) and not B.is_build({'kind': 'agency'})
    assert B.build_within_s(build_turn()) == 120
    assert B.build_within_s(build_turn(within=90)) == 90
    assert B.build_within_s(build_turn(within=0)) == 120                 # 没写时限：协议的 120 秒
    assert B.build_within_s(build_turn(within=999)) == 180
    assert B.build_within_s({'kind': 'agency', 'packet': {'isle': {'within_s': 120}}}) == 0
    assert B.build_max_chars(build_turn()) == 8000 and B.build_max_chars(build_turn(max_chars=0)) == B.BUILD_MAX_CHARS
    assert B.build_budget(build_turn()) == 4000
    p = B.build_prompt(build_turn(), seconds_left=117)
    assert p.startswith('【鱼缸岛 · 建模回合】你在岛上叫「小汤圆」 · 117 秒内回答')
    assert GUIDE in p and '两层的家，带个阳台' in p                       # 小镇的说明和她的想法原样给她
    assert '不超过 8000 字' in p and '{"skip":true}' in p and 'confirm_build' in p


def test_her_answer_tidied_only_skip_unreadable_empty_too_long():
    t = build_turn()
    ok = B.read_build(t, '好的，这是我的楼：\n```json\n' + MODEL + '\n```')
    assert ok['answer'] == MODEL and ok['shapes'] == 2
    bare = B.read_build(t, json.dumps({'shapes': [{'type': 'box', 'size': [1, 1, 1]}]}))
    assert json.loads(bare['answer']) == {'shapes': {'shapes': [{'type': 'box', 'size': [1, 1, 1]}]}, 'kind': 'shapes'}
    assert json.loads(B.read_build(t, json.dumps({'kind': 'recipe', 'shapes': {'shapes': [{'type': 'box'}]}}))['answer'])['kind'] == 'recipe'
    assert B.read_build(t, '{"skip":true}')['answer'] == B.SKIP
    assert B.read_build(t, '这次先不盖了 {"skip": true}')['answer'] == B.SKIP
    assert B.read_build(t, '')['why'] == 'empty'
    assert B.read_build(t, '{"kind":"shapes","shapes":{"shapes":[{"type":"box","size":[1,')['why'] == 'unreadable'
    assert B.read_build(t, '{"kind":"shapes","shapes":{"shapes":[]}}')['why'] == 'no_model'
    assert B.read_build(t, '{"action":"none"}')['why'] == 'no_model'
    many = {'kind': 'shapes', 'shapes': {'shapes': [{'type': 'box', 'pos': [i, 0, 0], 'size': [1, 1, 1], 'material': 'wood'} for i in range(200)]}}
    long = B.read_build(t, json.dumps(many))
    assert long['answer'] is None and long['why'] == 'long' and long['chars'] > 8000
    assert f"有 {long['chars']} 字，超过了 8000 字" in B.build_again('long', max_chars=8000, chars=long['chars'])
    assert '写到一半断了' in B.build_again('unreadable')


def test_no_style_or_look_advice_anywhere_the_plugin_talks_about_building():
    texts = [B.build_prompt(build_turn()).replace(GUIDE, '').replace(IDEA, ''), B.build_again('long'), B.build_again('unreadable'),
             B.build_again('no_model'), ISLE_BUILD_NOTE, BUILD_STEP]
    for text in texts:
        for w in STYLE_WORDS:
            assert w not in text.lower(), (w, text[:60])
    assert '在岛上盖楼：' in guide_text() and '田只能种地' in guide_text()
    assert '鲸币' in BUILD_STEP and '¥' not in BUILD_STEP + ISLE_BUILD_NOTE and '元' not in BUILD_STEP + ISLE_BUILD_NOTE
    assert 'DSH' not in guide_text()


def test_island_decision_turns_say_how_to_build():
    isle = {'turn_id': 'i', 'kind': 'agency', 'packet': {'isle': {'static': 'a1b2c3d4e5f6', 'within_s': 120}, 'messages': [], 'reply': {'format': 'json'}}}
    assert ISLE_BUILD_NOTE in build_turn_prompt(isle, seconds_left=110)
    assert '要和主人一起盖' in ISLE_BUILD_NOTE and '"do":"build","lot"' not in ISLE_BUILD_NOTE and '"verb":"建模"' in ISLE_BUILD_NOTE   # 0.4.7：回合里不盖楼
    assert ISLE_BUILD_NOTE not in build_turn_prompt({'turn_id': 'p', 'kind': 'agency', 'packet': {'messages': [], 'reply': {'format': 'json'}}})


def test_build_guide_is_read_inside_the_turn_and_the_whole_howto_page_comes_back(tmp_path):
    assert I.read_of('{"action":"none","world":{"do":"build_guide","lot":"L-0107"}}') == {'do': 'look', 'lot': 'L-0107', 'topic': '怎么做', 'verb': '建模'}
    page = '建模说明' + '量' * 3200

    class Client:
        reads = []

        async def isle_read(self, tid, read):
            self.reads.append(read)
            return {'ok': True, 'text': page if read['topic'] == '怎么做' else '地' * 3000, 'next': None}
    isle = I.Isle(Client(), Journal(tmp_path / 'j.json'))
    asked, answers = [], ['{"action":"none","world":{"do":"look","topic":"地"}}', '{"action":"none","world":{"do":"build","lot":"L-0107","idea":"一个家"}}']

    async def ask(text, images=None):
        asked.append(text)
        return answers.pop(0)
    r = asyncio.run(isle.read_loop({'turn_id': 't'}, '{"action":"none","world":{"do":"build_guide","lot":"L-0107"}}', ask=ask,
                                   to_answer=lambda x: x, left=lambda: 100))
    assert r['reads'] == 2
    assert asked[0].startswith('【你刚查的：建模说明】') and page in asked[0]
    assert I.HOWTO_TEXT_MAX >= 6000 and I.READ_TEXT_MAX == 1500 and len(asked[1]) < 1500 + 400
    assert json.loads(r['answer'])['world'] == {'do': 'build', 'lot': 'L-0107', 'idea': '一个家'}


def notes(r):
    return [e['text'] for e in r.journal.s['journal'] if e.get('ev') == 'build']


# --- the runner -----------------------------------------------------------------------------------------------------------
class Town:
    def __init__(self):
        self.posted = []

    async def answer(self, tid, body):
        self.posted.append((tid, body))
        return {'ok': True, 'receipt': {'ok': True, 'reason': '交上了，等量尺寸'}}


def run_build(tmp_path, reply, turn=None):
    t = Town()
    asks = []

    async def ask(prompt, images=None):
        asks.append(prompt)
        r = reply(len(asks))
        if isinstance(r, BaseException):
            raise r
        return r
    r = Runner(t, Journal(tmp_path / 'j.json'), ask)
    r.running = True
    turn = turn or build_turn()
    real = r._ask
    limits = []

    async def spy(prompt, timeout, images=None):
        limits.append(timeout)
        return await real(prompt, timeout, images)
    r._ask = spy
    asyncio.run(r.handle(turn, time.time()))
    return t, asks, limits, r


def test_a_build_turn_one_ask_with_the_turns_long_time_limit_and_her_model_goes_as_written(tmp_path):
    t, asks, limits, r = run_build(tmp_path, lambda n: '```json\n' + MODEL + '\n```')
    assert len(asks) == 1                                                 # 一个建模回合一次调用
    assert 100 < limits[0] <= 120, limits                                # 回合的 120 秒，不是 23 秒
    assert '建模回合' in asks[0] and not asks[0].startswith('〔岛上的规矩〕')
    assert t.posted == [('ot_build', MODEL)]
    assert r.stats['builds'] == 1 and r.stats['answered'] == 1
    assert notes(r) == ['你在建模回合（地 L-0107）交了一份 2 个形状的楼，等镇上量尺寸、报价；报价出来后写 confirm_build 才扣鲸币']


def test_a_build_turn_without_deadline_or_within_still_gets_120_s(tmp_path):
    _, _, limits, _ = run_build(tmp_path, lambda n: MODEL, turn=build_turn('ot_b2', within=0, deadline=False))
    assert limits[0] > 100


def test_she_skips_this_time(tmp_path):
    t, _, _, r = run_build(tmp_path, lambda n: '{"skip":true}')
    assert t.posted == [('ot_build', B.SKIP)] and r.stats.get('builds', 0) == 0


def test_unreadable_twice_one_retry_at_most_then_skip_no_model_made_up(tmp_path):
    t, asks, _, r = run_build(tmp_path, lambda n: '{"kind":"shapes","shapes":{"shapes":[{"type":"box","si')
    assert len(asks) == 2 and '写到一半断了' in asks[1]                    # 和别的回合一样最多再问一次
    assert t.posted == [('ot_build', B.SKIP)] and r.stats['invalid'] == 1
    assert notes(r) == ['小镇给你的建模回合（地 L-0107）没交上（交的答案读不了），这次什么都没盖；想盖再写一次想法']   # 小镇那边只知道 skip


def test_too_long_first_she_cuts_it_down_once(tmp_path):
    many = json.dumps({'kind': 'shapes', 'shapes': {'shapes': [{'type': 'box', 'pos': [i, 0, 0], 'size': [1, 1, 1]} for i in range(200)]}})
    t, asks, _, _ = run_build(tmp_path, lambda n: many if n == 1 else MODEL)
    assert len(asks) == 2 and '超过了 8000 字' in asks[1]
    assert t.posted == [('ot_build', MODEL)]


def test_her_model_timed_out_or_failed_the_plugin_says_skip_at_once(tmp_path):
    for i, err in enumerate((asyncio.TimeoutError(), RuntimeError('provider 500'))):
        (tmp_path / str(i)).mkdir()
        t, asks, _, r = run_build(tmp_path / str(i), lambda n, e=err: e)
        assert len(asks) == 1 and t.posted == [('ot_build', B.SKIP)], repr(err)
        assert ('你的模型没在时限内答完' if i == 0 else '你的模型这次出错了') in notes(r)[0]
    assert B.build_note(build_turn(), skip=True) == '小镇给你的建模回合（地 L-0107）你这次没交，什么都没盖'
