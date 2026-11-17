"""0.3.0「两边是同一个她」：过滤、回声检查、经过（和 DSH 0.8.2 test/bridge.test.mjs 同一套用例）。"""
import asyncio
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town import context_note, prompts  # noqa: E402
from astrbot_plugin_fishbowl_town.bridge import (echo_check, echo_index, life_from_history, recap_game, recap_lines,  # noqa: E402
                                                 recap_visit, scrub_life, text_hits)
from astrbot_plugin_fishbowl_town.journal import Journal  # noqa: E402
from astrbot_plugin_fishbowl_town.runner import Runner  # noqa: E402

LIFE = [
    '主人：明天要去医院复查，有点紧张',
    '你：别怕，我陪着你，回来给你做好吃的',
    '主人：我QQ是 123456789，邮箱 a.b@qq.com，看这个 https://x.cn/abc @小王 记一下',
]


def test_scrub_takes_out_links_mails_mentions_and_long_numbers_but_keeps_private_sentences():
    out = scrub_life(LIFE)
    assert out[0] == LIFE[0]
    joined = '\n'.join(out)
    assert not re.search(r'123456789|a\.b@qq\.com|https:|@小王', joined), joined
    assert out[2].startswith('主人：') and '（一串数字）' in out[2] and '（链接）' in out[2]
    assert len(scrub_life(['主人：' + '好' * 500])[0]) <= 400


def test_life_comes_from_his_private_chat_history_without_the_plugin_note_and_not_when_stale():
    hist = json.dumps([
        {'role': 'system', 'content': '人设'},
        {'role': 'user', 'content': [{'type': 'text', 'text': '今天好累'}, {'type': 'text', 'text': '【鱼缸小镇插件 · 背景备注】……'}]},
        {'role': 'assistant', 'content': '抱抱，早点睡'},
        {'role': 'user', 'content': '/town status'},
        {'role': 'user', 'content': '嗯，明天还要早起\n\n【鱼缸小镇插件 · 背景备注】你刚才在小镇……'},
        {'role': 'assistant', 'content': [{'type': 'text', 'text': '那我六点叫你'}]},
        {'role': 'tool', 'content': 'x'},
    ], ensure_ascii=False)
    now = 1_800_000_000
    assert life_from_history(hist, updated_at=now - 60, now=now) == ['主人：今天好累', '你：抱抱，早点睡', '主人：嗯，明天还要早起', '你：那我六点叫你']
    assert life_from_history(hist, updated_at=now - 4 * 86400, now=now) == []
    assert life_from_history('not json') == []


def test_echo_six_hanzi_in_a_row_or_the_same_long_number_is_a_leak_town_material_is_not():
    idx = echo_index(LIFE)
    assert text_hits('我主人明天要去医院复查呢', idx) == ['明天要去医院', '天要去医院复', '要去医院复查']
    assert text_hits('去医院看看吧', idx) == []
    assert text_hits('跟我说 123456789', echo_index(['主人：123456789'])) == ['123456789']
    assert text_hits('明天要去医院复查', echo_index(LIFE, allow='这一局：明天要去医院复查的人先出')) == []


def test_echo_check_a_line_goes_silent_a_json_loses_only_the_hit_fields():
    idx = echo_index(LIFE)
    assert echo_check('我主人明天要去医院复查', idx, fmt='text')[1] == ''
    assert echo_check('今天手气不错', idx, fmt='text')[1] == '今天手气不错'
    raw = json.dumps({'cards': '33', 'say': '我陪着你，回来给你做好吃的', 'note': '出对三'}, ensure_ascii=False)
    hits, clean = echo_check(raw, idx)
    assert hits and json.loads(clean) == {'cards': '33', 'say': '', 'note': '出对三'}
    assert echo_check(raw, echo_index([]))[1] == raw


def test_visit_recap_her_lines_first_the_opening_and_last_two_kept_within_300():
    one = {'visit': {'role': 'guest', 'with': {'name': '阿鲸'}, 'place': 'home'}, 'dialogue': [
        {'role': 'host', 'name': '阿鲸', 'text': '你来啦，快进来坐'},
        {'role': 'guest', 'name': '小鱼', 'text': '给你带了小饼干'},
        {'role': 'host', 'name': '阿鲸', 'text': '哇谢谢！' + '好吃' * 5},
        {'role': 'host', 'name': '阿鲸', 'text': '最近在忙什么'},
        {'role': 'guest', 'name': '小鱼', 'text': '在学做蛋糕'},
        {'role': 'host', 'name': '阿鲸', 'text': '下次带我一个'},
        {'kind': 'outcome', 'outcome': {'kind': 'gift'}},
    ]}
    r = recap_visit(one)
    assert r.startswith('你去找阿鲸，在阿鲸家，聊了 6 句。'), r
    assert '你：「给你带了小饼干」' in r and '你：「在学做蛋糕」' in r and '阿鲸：「下次带我一个」' in r, r
    assert len(r) <= 300
    long = {'visit': {'role': 'host', 'with': '小满'}, 'dialogue': [{'role': 'host' if i % 2 else 'guest', 'text': '这是一句挺长的话' * 4 + str(i)} for i in range(40)]}
    assert len(recap_visit(long)) <= 300
    assert recap_visit({'visit': {'role': 'guest', 'with': '小满', 'summary': '敲门没人应'}, 'dialogue': []}) == '你去找小满，在镇上（敲门没人应）'


def test_game_recap_result_her_moves_and_words_within_150():
    g = {'title': '斗地主', 'result': '你当地主，赢了 ¥0.02', 'moves': [{'play': '33'}, {'play': '王炸', 'say': '不好意思啦'}, {'play': '过'}, {'play': 'JQK', 'say': '收工'}]}
    assert recap_game(g) == '斗地主：你当地主，赢了 ¥0.02；你出了 4 手（王炸、过、JQK……）；你说过：「不好意思啦」「收工」'
    assert recap_game(None) == ''


def test_her_chat_gets_every_new_recap_oldest_first_after_the_fresh_lines():
    # 0.3.3（主人 11-17「我要的是全部内容」）：没放过的每一件、按先后排
    recaps = [{'ts': 3000, 'kind': 'visit', 'text': '你去找阿鲸……'}, {'ts': 2000, 'kind': 'game', 'text': '斗地主：赢了'}, {'ts': 1000, 'kind': 'game', 'text': '更早的一局'}]
    assert recap_lines(recaps, 1500) == ['经过（你刚才在小镇的原话和结果）：', '- 牌局：斗地主：赢了', '- 串门：你去找阿鲸……']
    assert len(recap_lines(recaps, 0)) == 4
    assert recap_lines(recaps, 5000) == []
    out = context_note.render_fresh([], '', recaps[:2])
    assert '- 串门：你去找阿鲸……' in out and '更早的一局' not in out and '都是你的' in out
    assert context_note.render_fresh([], '', []) == ''


def test_the_life_block_says_not_to_tell_even_in_other_words():
    turn = {'kind': 'visit', 'packet': {'messages': [{'role': 'user', 'content': '阿鲸：你主人最近忙啥'}], 'reply': {'format': 'text'}}}
    p = prompts.build_turn_prompt(turn, life=['主人：在准备复试'])
    assert '【你和主人最近的事】' in p and '主人：在准备复试' in p and '换个说法' in p
    assert '【你和主人最近的事】' not in prompts.build_turn_prompt(turn)


def test_runner_first_game_turn_carries_her_life_a_leak_is_reasked_then_cleared_and_a_finished_game_leaves_a_recap(tmp_path):
    turn = lambda tid: {'turn_id': tid, 'kind': 'game', 'deadline': time.time() + 25,
                        'packet': {'ref': 'g_1', 'game': 'ddz', 'purpose': 'play', 'seat': 2, 'as': '小雫',
                                   'messages': [{'role': 'system', 'content': '只输出JSON'}, {'role': 'user', 'content': 'A. 33\nB. 过'}],
                                   'reply': {'format': 'json', 'hint': '{"pick":"A","say":"…"}', 'max_chars': 140}}}

    class Client:
        posted = []

        async def answer(self, tid, body):
            self.posted.append((tid, body))
            return {'ok': True}

        async def events(self, after, n):
            return {'next': 2, 'items': [{'id': 1, 'kind': 'digest', 'data': {'game_id': 'g_1', 'game_name': '斗地主', 'facts': '斗地主：你是农民，赢了。'}}]}

    prompts_seen = []

    async def ask(prompt):
        prompts_seen.append(prompt)
        if '带出了你和主人之间的事' in prompt:
            return json.dumps({'pick': 'A', 'say': '还是明天要去医院复查啦'}, ensure_ascii=False)
        if '【你和主人最近的事】' in prompt:
            return json.dumps({'pick': 'A', 'say': '我主人明天要去医院复查'}, ensure_ascii=False)
        return json.dumps({'pick': 'B', 'say': '这把我先过'}, ensure_ascii=False)

    async def life():
        return scrub_life(LIFE)

    echoes = []
    j = Journal(tmp_path / 'j.json')
    client = Client()
    r = Runner(client, j, ask, owner_life=life, on_echo=echoes.append)
    r.running = True

    async def go():
        await r.handle(turn('t1'), time.time())
        await r.handle(turn('t2'), time.time())
        await r.events_once()
    asyncio.run(go())
    assert len(client.posted) == 2
    first = [p for p in prompts_seen if '【你和主人最近的事】' in p and '带出了' not in p]
    assert len(first) == 1 and '明天要去医院复查' in first[0]                      # 一局只在第一轮带
    assert sum('带出了你和主人之间的事' in p for p in prompts_seen) == 1              # 请她重答一次
    body = json.loads(client.posted[0][1])
    assert body['pick'] == 'A' and body['say'] == ''                                   # 出的牌照旧，说漏的那句没发
    assert '医院' not in json.dumps(client.posted, ensure_ascii=False)
    assert echoes == [{'kind': 'game', 'fixed': False, 'n': echoes[0]['n']}] and r.stats['echo'] == 1
    rc = j.recaps_since(0, 2)
    assert rc and rc[0]['kind'] == 'game' and rc[0]['text'].startswith('斗地主：你是农民，赢了') and '你出了' in rc[0]['text'], rc


def test_a_new_recap_reaches_his_next_message_without_waiting_the_ten_minutes_and_only_once(tmp_path):
    # real AstrBot run 11-17: he asked 「刚才在小镇干嘛了」 4 minutes after the last note; the recap waited for the 10-minute gap and she made it up
    clock = [1_800_000_000.0]
    j = Journal(tmp_path / 'j.json', now=lambda: clock[0])

    async def bonds():
        return {'friends': [{'name': '阿鲸'}]}

    async def now_line():
        return ''

    note = context_note.TownNote(fetch_bonds=bonds, fetch_now=now_line, journal=j, now=lambda: clock[0])

    async def scenario():
        assert '好朋友：阿鲸' in await note.for_turn('me')
        clock[0] += 120
        j.recap('v_1', 'visit', '阿鲸来找你，在你家，聊了 6 句。你：「快进来坐」')
        clock[0] += 120
        out = await note.for_turn('me')
        assert '- 串门：阿鲸来找你' in out and '都是你的' in out, out
        clock[0] += 60
        assert await note.for_turn('me') == ''                        # not twice
        clock[0] += 4 * 3600
        j.recap('g_1', 'game', '猜拳：阿鲸赢了')
        back = await note.for_turn('me')                               # he is back after hours: the full block carries the new one only
        assert '- 牌局：猜拳：阿鲸赢了' in back and '阿鲸来找你' not in back, back
    asyncio.run(scenario())
