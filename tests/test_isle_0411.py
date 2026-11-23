"""0.4.11 说明和工具跟上鱼缸岛 11-23 14:55 上线的那一批（地租减半、大户费分三类、竞拍到点后最多 1 小时随机结束、建楼和室内降价、
自由建模、银行存取、自己的地和楼挂出去卖、开张后换店的类、查「玩的」、楼号 B-… 当 lot）。和 DSH 插件 0.9.15 test/isle-0915.test.mjs 同一份。"""
import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT / 'tests'))

from astrbot_plugin_fishbowl_town import isle_tools as T  # noqa: E402
from astrbot_plugin_fishbowl_town.guide import AUCTION_RULE, BANK_RULE, BUILD_FREE, BUILD_STEP, RENT_RULE, TAX_RULE, guide_text  # noqa: E402
from astrbot_plugin_fishbowl_town.isle import build_read_followup  # noqa: E402

from test_isle_tools import Client, Journal, no_sleep  # noqa: E402

README = (ROOT / 'README.md').read_text(encoding='utf-8')
VENDORED = (ROOT / 'cobuild_scripts' / 'README.md').read_text(encoding='utf-8')
MAIN = (ROOT / 'main.py').read_text(encoding='utf-8').replace('\r\n', '\n')


def tool_doc(name):
    m = re.search(r"@filter\.llm_tool\(name='" + name + r"'\)\n    async def \w+\(self, event: AstrMessageEvent[^)]*\):\n        \"\"\"([\s\S]*?)\"\"\"", MAIN)
    assert m, name
    return m.group(1)


def test_tax_sentence_and_one_hour_auction_tail_in_guide_and_readme():
    assert TAX_RULE == ('大户费分三类各算各的（同一个主人名下的鱼合起来算）：田、果园、牧场按块数，前 3 块不交，第 4–6 块每块另交它地租的 1 倍，第 7 块起 3 倍；'
                        '商铺、住房按面积，各有 1000 平方米不交：超出的面积占这一类的几成，这一类每块地就另交它地租的几成（1000–3000 平方米那部分按 1 倍算，3000 平方米以上那部分按 3 倍算）')
    assert '到点以后再挂最多 1 小时，随机结束' in AUCTION_RULE and '这一小时只有出过价的鱼能接着加价' in AUCTION_RULE
    for where, text in (('新手引导', guide_text()), ('README', README)):
        for rule in (RENT_RULE, TAX_RULE, AUCTION_RULE, BANK_RULE, BUILD_FREE):
            assert rule in text, (where, rule[:14])
        assert '11 月 25 日起收' in text and '先抵大户费、再抵地租' in text, where
        assert '成交价的 95% 给她，拿到满 7 天才能卖' in text, where
    assert BUILD_FREE in BUILD_STEP


def test_no_stale_numbers_left():
    said = {'新手引导': guide_text(), 'README': README, '插件自带的建模说明': VENDORED, '做一件事': tool_doc('fishbowl_isle_act'),
            '看岛上': tool_doc('fishbowl_isle_look'), '一起盖楼': tool_doc('fishbowl_town_build')}
    for where, text in said.items():
        for old in ('0.2%', '最多 3 小时', '再挂 3 小时', '12 米高', '512 KB', '起步 50 鲸币', '每平方米 1 鲸币', '每天每平方米 0.05', '每 MB 每天 20 鲸币', '每一万面每天 10 鲸币'):
            assert old not in text, (where, old)
    for w in ('高：20–40 米 每米 18.75 鲸币', '体积：20000 立方米以上每立方米 0.05 鲸币', '文件：2048–6144 KB 每 MB 100 鲸币', '起步 20 + 96 鲸币，房间 24 间'):
        assert w in README, w
    assert '起步 20 鲸币 + 每平方米 0.2 鲸币' in VENDORED and '这 14 种是质感，每种都能配任意 `color`；用哪几种、什么颜色不限' in VENDORED and '样子不限' in VENDORED
    build = tool_doc('fishbowl_town_build')
    assert '什么材质和颜色、主人贴不贴自己的图，都由你们定，不限；只守地界和上限' in build and '承包的楼也可以填楼号' in build


def test_play_places_are_a_topic_of_their_own():
    assert '玩的' in T.LOOK_TOPICS and '住宿' not in T.LOOK_TOPICS
    assert T.read_of('玩的', more='{"kind":"狼人杀"}') == {'topic': '玩的', 'kind': '狼人杀'}
    page = '—— 岛上鱼开的（1 处）——\ns_6b7a4003「两张凳一张桌」温鱼（外来的鱼）开的牌桌：门后的世界（一个人玩）；246 米'
    c = Client(look={'ok': True, 'topic': '玩的', 'page': 1, 'items': [], 'text': page, 'next': None})
    out = asyncio.run(T.look(c, T.read_of('玩的')))
    assert c.reads[0] == {'topic': '玩的'}
    assert out == f'【岛上 · 能玩的地方】\n{page}'
    assert '玩的（能玩的地方：岛上鱼开的门后世界和游戏店、镇上的桌，带怎么坐' in tool_doc('fishbowl_isle_look')
    assert build_read_followup({'topic': '玩的'}, page, 2).startswith('【你刚查的：能玩的地方】')


def test_bank_sell_unsell_facade_kind_and_b_id_go_to_the_island_as_written():
    worlds = [{'do': 'bank', 'save': 100}, {'do': 'bank', 'take': 40}, {'do': 'bank'}, {'do': 'sell', 'lot': 'B-0009', 'floor': 800},
              {'do': 'unsell', 'lot': 'L-0107'}, {'do': 'facade', 'spot': 's_6b7a4003', 'kind': '游戏', 'kind_word': '牌桌'}, {'do': 'demolish', 'lot': 'B-0009'}]
    c = Client(act=lambda w: {'ok': True, 'text': f"收到 {w['do']}"})
    j = Journal()
    for w in worlds:
        r = asyncio.run(T.act(c, w, journal=j, sleep=no_sleep))
        assert f"岛上回你的：「收到 {w['do']}」" in r, r
    assert [w for w, _ref in c.acts] == worlds
    assert '存取银行' in j.j[0][1] and '把地和楼挂出去卖' in j.j[3][1] and '撤下挂着卖的地' in j.j[4][1]
    doc = tool_doc('fishbowl_isle_act')
    for w in ('{"do":"bank","save":100}', '"take" 取', '{"do":"sell","lot":"地号或楼号","floor":底价}', 'unsell 撤下', '改门面和店的类'):
        assert w in doc, w
    assert not re.search('赚|划算|趁|该买|该卖|充值吧|去充值', doc)

