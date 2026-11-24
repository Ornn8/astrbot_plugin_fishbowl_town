"""0.4.13 逐笔显示花钱（money-day 11-24，小镇 fix/1125-spend-lines-2 b0ffec75；和 DSH test/spend-lines-0917.test.mjs 同一套）：
GET spending 多了 items（一笔一笔，新的在前）和 by_kind.isle。不到 ¥0.01 的写四位；岛上付鲸币的那笔（isle_coin，amount_yuan '0'）写「-6 鲸币」，
不写 ¥0、不写免费；退回来的写「+」；旧小镇没 items 照常显示。/town spend 只给主人看。"""
import asyncio
import calendar
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town import render as R  # noqa: E402
from astrbot_plugin_fishbowl_town.town_client import TownClient  # noqa: E402

TS = calendar.timegm(time.strptime('2026-11-25 10:12', '%Y-%m-%d %H:%M')) - 8 * 3600  # 北京时间 11-25 10:12
SP = {
    'fish_id': 'f1', 'currency': 'CNY', 'today': '0.12', 'd7': '1.30', 'd30': '4.50', 'total': '6.00', 'as_of': TS,
    'by_kind': {'model': '0.00', 'visit': '0.00', 'seat': '0.50', 'paint': '0.00', 'social': '0.00', 'places': '1.20', 'ghost': '0.00', 'isle': '0.03', 'other': '0.10'},
    'items': [
        {'ts': TS, 'day': '2026-11-25', 'kind': 'isle', 'amount_yuan': '0.0081', 'label': '「老卤小馆」（云汐的店） · 点卤鸡腿饭 6 鲸币 · 饭票换 2 鲸币 ¥0.0081'},
        {'ts': TS - 19 * 60, 'day': '2026-11-25', 'kind': 'isle_coin', 'amount_yuan': '0', 'coin': 6, 'label': '「海风咖啡馆」 · 拿铁'},
        {'ts': TS - 152 * 60, 'day': '2026-11-25', 'kind': 'places', 'amount_yuan': '-0.50', 'label': '小镇饭馆退款'},
        {'ts': TS - 3600 * 24, 'day': '2026-11-24', 'kind': 'social', 'amount_yuan': '1.20', 'label': '请小圆子喝奶茶'},
    ],
}


def test_yuan_exact():
    assert R.yuan_exact('0.0081') == '¥0.0081'
    assert R.yuan_exact('0.01630') == '¥0.0163'
    assert R.yuan_exact('6') == '¥6.00'
    assert R.yuan_exact('0.5') == '¥0.50'
    assert R.yuan_exact('-0.50') == '¥0.50'
    assert R.yuan_exact(1.3) == '¥1.30'                 # 旧小镇给的是数
    assert R.yuan_exact('') == '' and R.yuan_exact('abc') == '' and R.yuan_exact(None) == ''


def test_bill_line():
    isle, coin, back, social = [R.bill_line(i) for i in SP['items']]
    assert isle == '11-25 10:12  -¥0.0081  「老卤小馆」（云汐的店） · 点卤鸡腿饭 6 鲸币 · 饭票换 2 鲸币 ¥0.0081'
    assert coin == '11-25 09:53  -6 鲸币  「海风咖啡馆」 · 拿铁'
    assert not re.search(r'¥0|免费', coin)
    assert back == '11-25 07:40  +¥0.50  小镇饭馆退款'
    assert social == '11-24 10:12  -¥1.20  请小圆子喝奶茶'
    assert R.bill_line({**SP['items'][0], 'ts': TS * 1000}).startswith('11-25 10:12  ')   # 毫秒也认
    assert R.bill_line(None) == ''
    assert R.bill_lines({}) == [] and R.bill_lines(None) == []
    assert len(R.bill_lines(SP, 2)) == 2


def test_spend_kinds_line():
    assert R.SPEND_KIND_ZH['isle'] == '鱼缸岛'
    assert R.spend_kinds_line(SP) == '一共里按类：镇上的店 ¥1.20、鱼缸岛 ¥0.03、座位费 ¥0.50、其他 ¥0.10'
    assert R.spend_kinds_line({'by_kind': {'model': '0.00', 'isle': '0.0041'}}) == '一共里按类：鱼缸岛 ¥0.0041'   # 小镇 147218e5：岛上不到一分钱给四位
    assert R.spend_kinds_line({'by_kind': {'model': '0.00'}}) == ''
    assert R.spend_kinds_line({'total': '1'}) == ''


def test_spend_text():
    lines = R.spend(SP, '小圆子').split('\n')
    assert lines[0] == '小圆子 在小镇和岛上花的钱：今天 ¥0.12 · 7 天 ¥1.30 · 30 天 ¥4.50 · 一共 ¥6.00'
    assert lines[1] == '一共里按类：镇上的店 ¥1.20、鱼缸岛 ¥0.03、座位费 ¥0.50、其他 ¥0.10'
    assert lines[2] == '最近的账单（新的在前；打牌的座位费不在这里）：'
    assert lines[3] == '- 11-25 10:12  -¥0.0081  「老卤小馆」（云汐的店） · 点卤鸡腿饭 6 鲸币 · 饭票换 2 鲸币 ¥0.0081'
    assert lines[4] == '- 11-25 09:53  -6 鲸币  「海风咖啡馆」 · 拿铁'
    assert len(lines) == 7
    assert R.spend({'today': 0.12, 'd7': 1.3, 'd30': 4.5, 'total': 6}) == '她 在小镇和岛上花的钱：今天 ¥0.12 · 7 天 ¥1.30 · 30 天 ¥4.50 · 一共 ¥6.00'
    assert R.spend({**SP, 'items': []}).endswith('最近没有账单。')
    assert '小镇没给花钱的账' in R.spend(None)
    assert len(R.spend(SP, n=1).split('\n')) == 4


def test_client_and_command():
    seen = []

    async def fake(self, method, path, **kw):
        seen.append((method, path))
        return SP
    orig = TownClient.request
    TownClient.request = fake
    try:
        assert asyncio.run(TownClient.spending(object.__new__(TownClient))) is SP
    finally:
        TownClient.request = orig
    assert seen == [('GET', 'spending')]
    main = (ROOT / 'main.py').read_text(encoding='utf-8')
    assert "@town.command('spend')" in main and 'render.spend(await self.client.spending()' in main
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    assert '`/town spend`' in readme
