"""0.4.2 鱼缸岛的回合瘦身（和 DSH 0.9.4 test/isle.test.mjs 同一套用例）：规矩按版本存一份、放在每一问最前面；
一回合里先查再出手（最多 3 次）；岛上拿主意的回合按 packet.isle.within_s 算时限。"""
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town import isle as I  # noqa: E402
from astrbot_plugin_fishbowl_town.journal import Journal  # noqa: E402
from astrbot_plugin_fishbowl_town.prompts import ISLE_LOOK_NOTE, build_turn_prompt  # noqa: E402
from astrbot_plugin_fishbowl_town.runner import Runner  # noqa: E402

V1, V2 = 'a1b2c3d4e5f6', 'ffeeddccbbaa'
BLOCKS = {'rules': '岛上只用贝。', 'verbs': ['claim 圈地', 'bid 出价'], 'goods': '粮 鱼 木 石', 'law': '不许场外买卖。'}


def isle_turn(tid, version=V1, within=120):
    return {'turn_id': tid, 'kind': 'agency', 'deadline': time.time() + within,
            'packet': {'purpose': 'agency', 'reasons': ['pulled'], 'isle': {'static': version, 'within_s': within},
                       'messages': [{'role': 'system', 'content': '拿主意的规则'}, {'role': 'user', 'content': '{"我":{"贝":800},"查":"地 楼 铺位 店"}'}],
                       'reply': {'format': 'json', 'hint': '{"action":"none"}', 'max_chars': 400}}}


def test_helpers():
    assert I.isle_version_of(isle_turn('t')) == V1
    assert I.isle_version_of({'packet': {'isle': {'static': 'not hex!'}}}) == '' and I.isle_version_of({'packet': {}}) == ''
    assert I.isle_within_s(isle_turn('t')) == 120 and I.isle_within_s({'packet': {'isle': {'within_s': 900}}}) == 180
    assert I.isle_within_s({'packet': {}}) == 0
    text = I.rules_text(BLOCKS)
    assert text == I.rules_text(dict(reversed(list(BLOCKS.items()))))
    assert text.startswith('〔岛上的规矩〕\n岛上只用贝。\n〔能做的事') and 'claim 圈地\nbid 出价\n〔标准货品〕' in text
    assert I.read_of('{"action":"none","world":{"do":"look","topic":"地","near":"镜湖","max_coin":800}}') == {'do': 'look', 'topic': '地', 'near': '镜湖', 'max_coin': 800}
    assert I.read_of('{"world":{"do":"claim"}}') is None and I.read_of('读不了') is None
    assert json.loads(I.drop_look('{"action":"pier","world":{"do":"look","topic":"地"}}')) == {'action': 'pier'}
    assert json.loads(I.drop_look('{"world":{"do":"look"}}'))['action'] == 'none'
    f = I.build_read_followup({'topic': '地'}, '1. 镜湖边 12×12', 2)
    assert f.startswith('【你刚查的：挂着的地】\n1. 镜湖边 12×12') and '还能查 2 次' in f
    assert '不再写 look' in I.build_read_followup({'topic': '地'}, 'x', 0)


def test_rules_fetched_once_per_version_kept_on_disk_and_kept_when_the_town_is_down(tmp_path):
    calls, st = [], {'fail': False, 'serve': V1}

    class Client:
        async def isle_static(self, have):
            calls.append(have)
            await asyncio.sleep(0.01)
            if st['fail']:
                raise RuntimeError('down')
            return {'version': st['serve'], 'same': True} if have == st['serve'] else {'version': st['serve'], 'blocks': {**BLOCKS, 'rules': '版本 ' + st['serve']}}

    j = Journal(tmp_path / 'j.json')
    isle = I.Isle(Client(), j)

    async def go():
        a, b = await asyncio.gather(isle.prefix(isle_turn('1')), isle.prefix(isle_turn('2')))
        assert len(calls) == 1 and a == b and '版本 a1b2c3d4e5f6' in a
        assert await isle.prefix(isle_turn('3')) == a and len(calls) == 1
        assert await isle.prefix({'turn_id': 'x', 'kind': 'agency', 'packet': {}}) == ''
        st['fail'] = True
        assert await isle.prefix(isle_turn('4', V2)) == a
        st['fail'], st['serve'] = False, V2
        assert '版本 ffeeddccbbaa' in await isle.prefix(isle_turn('5', V2))
    asyncio.run(go())
    assert calls == ['', V1, V1] and Journal(tmp_path / 'j.json').s['isle_static']['version'] == V2


class Town:
    def __init__(self, static_ok=True):
        self.posted, self.reads, self.static_ok = [], [], static_ok

    async def answer(self, tid, body):
        self.posted.append((tid, body))
        return {'ok': True}

    async def isle_static(self, have):
        if not self.static_ok:
            raise RuntimeError('404')
        return {'version': V1, 'blocks': BLOCKS}

    async def isle_read(self, tid, read):
        self.reads.append((tid, read))
        return {'ok': True, 'text': f"1. {read['topic']}：镜湖边 12×12，底价 720 贝", 'next': None}


def run_turn(tmp_path, answers, **town):
    t = Town(**town)
    prompts = []

    async def ask(prompt):
        prompts.append(prompt)
        return answers[min(len(prompts) - 1, len(answers) - 1)]
    r = Runner(t, Journal(tmp_path / 'j.json'), ask)
    r.running = True
    asyncio.run(r.handle(isle_turn('ot_isle'), time.time()))
    return t, prompts, r


def test_she_looks_first_gets_the_page_then_acts_and_the_rules_lead_every_ask(tmp_path):
    t, prompts, r = run_turn(tmp_path, [
        '{"action":"none","world":{"do":"look","topic":"地","near":"镜湖"}}',
        '{"action":"none","plan":{"hours":2,"doing":"去镜湖边圈地"},"world":{"do":"claim","near":"镜湖","w":12,"d":12,"max":800}}'])
    assert len(t.posted) == 1 and json.loads(t.posted[0][1])['world'] == {'do': 'claim', 'near': '镜湖', 'w': 12, 'd': 12, 'max': 800}
    assert t.reads == [('ot_isle', {'do': 'look', 'topic': '地', 'near': '镜湖'})]
    rules = I.rules_text(BLOCKS)
    assert len(prompts) == 2 and all(p.startswith(rules + '\n\n') for p in prompts)
    assert prompts[1].startswith(prompts[0]), 'the second ask carries the first on: same prefix for the cache'
    assert '【你刚查的：挂着的地】\n1. 地：镜湖边 12×12' in prompts[1] and '"do": "look"' in prompts[0]
    assert r.stats['isle_reads'] == 1 and r.left(isle_turn('x'), time.time()) > 100


def test_at_most_three_looks_then_the_look_is_dropped(tmp_path):
    t, prompts, r = run_turn(tmp_path, ['{"action":"pier","world":{"do":"look","topic":"店"}}'])
    assert len(t.reads) == I.READ_MAX and len(prompts) == 1 + I.READ_MAX + 1 and '查不了了' in prompts[-1]
    assert json.loads(t.posted[0][1])['action'] == 'pier' and 'world' not in json.loads(t.posted[0][1])
    assert r.stats['isle_reads'] == I.READ_MAX


def test_town_without_isle_routes_still_gets_her_answer(tmp_path):
    t, prompts, _ = run_turn(tmp_path, ['{"action":"none","plan":{"hours":1,"doing":"歇着"}}'], static_ok=False)
    assert len(t.posted) == 1 and not t.reads and prompts[0].startswith('【鱼缸小镇')


def test_not_an_island_turn_keeps_the_old_prompt_and_25s():
    plain = {'turn_id': 'p', 'kind': 'agency', 'packet': {'messages': [], 'reply': {'format': 'json'}}}
    assert ISLE_LOOK_NOTE not in build_turn_prompt(plain, seconds_left=20)
    assert ISLE_LOOK_NOTE in build_turn_prompt(isle_turn('i'), seconds_left=110)
    assert Runner(None, None, None).left(plain, time.time()) < 25
