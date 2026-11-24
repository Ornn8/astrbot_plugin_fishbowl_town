"""0.4.13（和 DSH test/transit-heart-0917.test.mjs 同一套）：远的店照样写，小镇带她走过去、到了照办（小镇 auto-transit 11-24：远处下单回
ok:true、code queued，到了的结果走下一回合的「变了」）；岛上碰面聊天里她写的表白 act {"kind":"heart"}（小镇 11-26）原样带回小镇。"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town import isle_tools as T  # noqa: E402
from astrbot_plugin_fishbowl_town.prompts import build_turn_prompt, hold_check, to_answer  # noqa: E402


def test_execute_far_shop_is_written_as_usual():
    turn = {'kind': 'agency', 'packet': {'actions': ['none'], 'messages': [{'role': 'system', 'content': '规则'}, {'role': 'user', 'content': '情况'}], 'reply': {'format': 'json'}}}
    text = build_turn_prompt(turn, role='execute', wish='去面包房买可颂', wish_own=True)
    assert '现在真办不了（关门、没这个、去不了），action 填 none' in text and '远的店照样写，小镇会带你走过去，到了照办。' in text
    assert '还没走到' not in text


def test_heart_act_goes_back_unchanged():
    turn = {'kind': 'visit', 'packet': {'reply': {'format': 'json', 'max_chars': 300}, 'messages': []}}
    ans = to_answer(turn, '{"say":"其实……我挺喜欢你的","act":{"kind":"heart","to":"小满"}}')
    assert json.loads(ans) == {'say': '其实……我挺喜欢你的', 'act': {'kind': 'heart', 'to': '小满'}}
    assert hold_check('visit', ans, ['spend', 'promise']) == ([], ans)


class Journal:
    def __init__(self):
        self.j = []

    def event(self, kind, text, tag='', limit=600):
        self.j.append(text)


class Client:
    SAID = '出发去「麦香面包房」，1000 米，13.9 分钟到。到了替你办「可颂」，4 鲸币到了才付，办好了下一回合告诉你'

    async def isle_act(self, world, ref=''):
        return {'ok': True, 'do': 'treat_self', 'code': 'queued', 'text': self.SAID}


async def no_sleep(_s):
    return None


def test_queued_far_order_is_on_its_way_not_done():
    j = Journal()
    out = asyncio.run(T.act(Client(), '{"do":"treat_self","svc":"food","item":"可颂"}', journal=j, sleep=no_sleep))
    assert out.startswith(f'岛上回你的：「{Client.SAID}」\n还没办完：出发了，到了照办，办好了小镇下一回合告诉你。（这是岛上刚回你的原话'), out
    assert '\n办成了' not in out
    assert j.j[0].startswith('你在聊天里在岛上去小店吃喝：出发去「麦香面包房」')
