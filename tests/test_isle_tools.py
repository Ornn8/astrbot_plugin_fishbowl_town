"""0.4.5 主聊天里的鱼缸岛（platform/接口-主聊天岛上工具.md）：fishbowl_isle_look / fishbowl_isle_act（和 DSH 插件 0.9.8 test/isle-tools.test.mjs 同一套用例）。"""
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town import isle_tools as T  # noqa: E402
from astrbot_plugin_fishbowl_town.isle import NO_PICS_HEAD  # noqa: E402
from astrbot_plugin_fishbowl_town.town_client import TownClient, TownError  # noqa: E402

from test_town_base import Net, Res  # noqa: E402

MAIN = (ROOT / 'main.py').read_text(encoding='utf-8')


def run(c):
    return asyncio.run(c)


async def no_sleep(_s):
    return None


class Journal:
    def __init__(self):
        self.j = []

    def event(self, kind, text, tag='', limit=600):
        self.j.append((kind, text))


class Client:
    def __init__(self, look=None, act=None):
        self.reads, self.acts = [], []
        self._look, self._act = look, act

    async def isle_look(self, read):
        self.reads.append(read)
        r = self._look(read) if callable(self._look) else self._look
        if isinstance(r, BaseException):
            raise r
        return r

    async def isle_act(self, world, ref=''):
        self.acts.append((world, ref))
        r = self._act(world) if callable(self._act) else self._act
        if isinstance(r, BaseException):
            raise r
        return r


def tool_doc(name):
    m = re.search(r"@filter\.llm_tool\(name='" + name + r"'\)\n    async def \w+\(self, event: AstrMessageEvent[^)]*\):\n        \"\"\"([\s\S]*?)\"\"\"\n([\s\S]*?)(?=\n    @|\n    # ---)", MAIN)
    assert m, name
    return m.group(1), m.group(2)


def test_the_two_tools_are_owner_only_main_chat_tools_with_the_doc_topics():
    assert T.LOOK_TOPICS == ('我的', '地', '楼', '铺位', '店', '订单', '缺的', '统计', '镇规', '怎么做', '我的店', '价', '地图', '样子', '田')
    for name in ('fishbowl_isle_look', 'fishbowl_isle_act'):
        doc, body = tool_doc(name)
        assert 'if not self._is_owner_chat(event):\n            return NOT_OWNER' in body, name
        assert 'return OFF_DUTY' in body
    doc, _ = tool_doc('fishbowl_isle_act')
    for w in ('fx_buy', '饭票', 'bid', 'take_building', 'claim', 'field_take', 'sprite_set', 'sprite_tall', 'open', 'owner_words', '不要劝他充值', 'fishbowl_town_go'):
        assert w in doc, w
    assert '¥' not in doc
    go, _ = tool_doc('fishbowl_town_go')
    assert 'fishbowl_isle_look' in go and 'fishbowl_isle_act' in go and 'plan' in go


def test_client_posts_read_and_world_with_ref():
    net = Net(lambda m, u: Res(200, '{"ok": true, "text": "好"}'))
    c = TownClient('fbo_test_code_123456', 'https://t.test/town/api/outside/', session=net)
    run(c.isle_look({'topic': '我的'}))
    run(c.isle_act({'do': 'bid', 'auction': 'A-0007', 'max': 600}, 'astr-1'))
    run(c.isle_act({'do': 'walk', 'to': '广场'}))
    assert [(s['method'], s['url']) for s in net.seen] == [('POST', 'https://t.test/town/api/outside/isle/look'),
                                                          ('POST', 'https://t.test/town/api/outside/isle/act'),
                                                          ('POST', 'https://t.test/town/api/outside/isle/act')]
    assert json.loads(net.seen[0]['data']) == {'read': {'topic': '我的'}}
    assert json.loads(net.seen[1]['data']) == {'world': {'do': 'bid', 'auction': 'A-0007', 'max': 600}, 'ref': 'astr-1'}
    assert json.loads(net.seen[2]['data']) == {'world': {'do': 'walk', 'to': '广场'}}


def test_read_of_and_world_of():
    assert T.read_of() == {'topic': '我的'}
    assert T.read_of('乱写') == {'topic': '我的'}
    assert T.read_of('地', near=' 广场 ', page=2, more='{"max_coin": 800, "min_m2": 100, "bad": {"x": 1}}') == {'topic': '地', 'near': '广场', 'page': 2, 'max_coin': 800, 'min_m2': 100}
    assert T.read_of('统计', more='not json', page=0) == {'topic': '统计'}
    assert T.world_of('{"do":"fx_buy","yuan":0.2}') == {'do': 'fx_buy', 'yuan': 0.2}
    assert T.world_of({'world': {'do': 'walk', 'to': '广场'}}) == {'do': 'walk', 'to': '广场'}
    assert T.world_of('{"world":{"do":"bid","auction":"A-1","max":5}}') == {'do': 'bid', 'auction': 'A-1', 'max': 5}
    for bad in ('', 'not json', '[1]', '{}', '{"do":"none"}', None):
        assert T.world_of(bad) is None, bad
    assert re.fullmatch(r'[A-Za-z0-9_-]{1,40}', T.new_ref()) and T.new_ref() != T.new_ref()


def test_look_mine_by_default_verbatim_and_failures_in_the_islands_words():
    mine = '鲸币 120.5（一个主人的鱼共用）\n饭票 ¥3.200\n我的：\nA-0007 海边 144 平方米：主人替你出的，最多出 500 鲸币，你领先'
    c = Client(look={'ok': True, 'topic': '我的', 'text': mine, 'next': None})
    assert run(T.look(c, T.read_of())) == f'【岛上 · 你自己的】\n{mine}'
    assert c.reads == [{'topic': '我的'}]
    c = Client(look={'ok': False, 'code': 'rate_limited', 'retry_after': 600, 'text': '主聊天里查岛上每小时最多 30 次，10 分钟后再查'})
    assert run(T.look(c, T.read_of('地'))) == '没查到：主聊天里查岛上每小时最多 30 次，10 分钟后再查'
    c = Client(look={'ok': False, 'code': 'bad', 'text': 'topic 只能是 地、楼、铺位、店、订单、缺的、统计、镇规、怎么做、我的店、价、地图、样子、田'})
    out = run(T.look(c, T.read_of()))
    assert out.startswith('没查到：topic 只能是') and '「我的」岛上还没开，先查「我的店」' in out


def test_look_with_pictures_gives_what_they_show():
    c = Client(look={'ok': True, 'topic': '地图', 'text': '广场一带的俯视图', 'pics': [{'url': 'https://api.fishbowltown.com/town/api/outside/isle/pic/0123456789ab.jpg', 'alt': '广场  北边 两块空地'}]})
    assert run(T.look(c, T.read_of('地图', near='广场'))) == f'【岛上 · 地图】\n广场一带的俯视图\n\n{NO_PICS_HEAD}\n① 广场 北边 两块空地'


def test_an_old_town_without_the_routes_answers_plainly():
    gone = TownError('HTTP_404', 404, '')
    c = Client(look=gone, act=gone)
    j = Journal()
    assert run(T.look(c, T.read_of())) == T.NOT_YET
    assert run(T.act(c, '{"do":"bid","auction":"A-1","max":5}', journal=j, sleep=no_sleep)) == T.NOT_YET
    assert 'fishbowl_town_go' in T.NOT_YET and j.j == []
    assert T.isle_fail(TownError('HTTP_405', 405)) == T.NOT_YET
    assert '没对外来的鱼开放' in T.isle_fail(TownError('TOWN_OFF', 404))
    assert re.search(r'太忙.*约 3 秒', T.isle_fail(TownError('TOWN_UNAVAILABLE', 503, '', 3)))
    assert run(T.look(object(), T.read_of())) == T.NOT_YET                  # an older client object
    assert run(T.act(None, '{"do":"walk","to":"广场"}')) == T.NOT_YET


def test_act_verbatim_framed_honestly_one_ref_per_action_and_a_journal_line():
    replies = [{'ok': True, 'do': 'bid', 'code': 'done', 'text': '出价收下了：你最多出 600 鲸币，现价 325 鲸币，你领先', 'ref': 'self_3f9a'},
               {'ok': False, 'do': 'claim', 'code': 'refused', 'text': '鲸币不够冻：要 750，你手上 120'},
               {'ok': False, 'do': 'walk', 'code': 'paused', 'text': '主人暂停了你，暂停的时候不能在岛上做事'}]
    c = Client(act=lambda w: replies.pop(0))
    j = Journal()
    out = run(T.act(c, '{"do":"bid","auction":"A-0007","max":600}', journal=j, sleep=no_sleep))
    assert out == '岛上回你的：「出价收下了：你最多出 600 鲸币，现价 325 鲸币，你领先」\n办成了。（这是岛上刚回你的原话。办成了就照这句告诉主人；没办成就照实说没办成、为什么，别说成办了。）'
    assert c.acts[0][0] == {'do': 'bid', 'auction': 'A-0007', 'max': 600} and re.fullmatch(r'astr-[0-9a-f]+-[0-9a-f]{6}', c.acts[0][1])
    assert j.j[0] == ('self', '你在聊天里在岛上出价：出价收下了：你最多出 600 鲸币，现价 325 鲸币，你领先')
    no = run(T.act(c, '{"do":"claim","near":"广场","w":12,"d":12,"max":750}', journal=j, sleep=no_sleep))
    assert no.startswith('岛上回你的：「鲸币不够冻：要 750，你手上 120」\n没办成（refused）。（这是岛上刚回你的原话')
    assert c.acts[1][1] != c.acts[0][1]
    assert j.j[1][1].startswith('你在聊天里想在岛上圈地，没成：鲸币不够冻')
    assert '「主人暂停了你，暂停的时候不能在岛上做事」\n没办成（paused）' in run(T.act(c, '{"do":"walk","to":"广场"}', sleep=no_sleep))
    assert len(c.acts) == 3                                                   # her owner's limits: no retry


def test_act_retries_once_with_the_same_ref_when_the_island_or_the_line_did_not_answer():
    answers = [{'ok': False, 'code': 'unavailable', 'text': '鱼缸岛这会儿没答上来，不知道办没办成'}, {'ok': True, 'code': 'done', 'text': '换到 20 鲸币，付了 ¥0.20 饭票'}]
    c = Client(act=lambda w: answers.pop(0))
    out = run(T.act(c, '{"do":"fx_buy","yuan":0.2}', owner_words='换两毛钱的鲸币吧', sleep=no_sleep))
    assert '「换到 20 鲸币，付了 ¥0.20 饭票」\n办成了' in out
    assert len(c.acts) == 2 and c.acts[0][1] == c.acts[1][1]
    c = Client(act=TownError('NETWORK', 0, 'reset'))
    assert '没连上小镇' in run(T.act(c, '{"do":"walk","to":"广场"}', sleep=no_sleep))
    assert len(c.acts) == 2 and c.acts[0][1] == c.acts[1][1]
    c = Client(act=TownError('TOWN_OUTSIDE_CODE', 401, '接入码不对'))
    run(T.act(c, '{"do":"walk","to":"广场"}', sleep=no_sleep))
    assert len(c.acts) == 1                                                   # a 4xx is not retried


def test_her_owners_money_only_with_his_words_and_building_is_not_here():
    c = Client(act={'ok': True, 'code': 'done', 'text': '好了'})
    for world in ('{"do":"fx_buy","yuan":0.5}', '{"do":"sprite_set","tall":1.6}'):
        out = run(T.act(c, world))
        assert re.search(r'花的是主人的饭票（他的真钱）.*owner_words 填他的原话.*不要劝他充值。什么都没扣', out)
        assert '充值：' not in out and '爱发电' not in out
    assert '花的是主人的饭票' in run(T.act(c, '{"do":"fx_buy","yuan":0.5}', owner_words='   '))
    assert c.acts == []
    assert '「好了」\n办成了' in run(T.act(c, '{"do":"fx_buy","yuan":0.5}', owner_words='去换五毛钱的鲸币', sleep=no_sleep))
    assert '办成了' in run(T.act(c, '{"do":"sprite_tall","tall":1.5}', sleep=no_sleep))
    assert len(c.acts) == 2
    for verb in ('build', 'build_guide', 'confirm_build'):
        assert run(T.act(c, json.dumps({'do': verb, 'lot': 'L-1'}))) == T.BUILD_HERE
    assert 'fishbowl_town_go' in T.BUILD_HERE
    assert 'fishbowl_isle_look' in run(T.act(c, '{"do":"look","topic":"地"}'))
    assert 'world 填' in run(T.act(c, '{}'))
    assert len(c.acts) == 2
