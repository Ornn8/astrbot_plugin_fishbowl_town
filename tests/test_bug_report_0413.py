"""0.4.13 报问题（fish-guide 11-24，小镇 feat/1125-isle-bugreport，11-25 包，开关默认开；和 DSH test/bug-report-0917.test.mjs 同一套）：
私聊里「在岛上做一件事」{"do":"report","about":"bug","why":"哪里不对"} 原样交给岛上，日记里写「报岛上的问题」；
不带 about 的 report 照旧是「举报一家店」。"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town import isle_tools as T  # noqa: E402


class Journal:
    def __init__(self):
        self.j = []

    def event(self, kind, text, tag='', limit=600):
        self.j.append(text)


class Client:
    def __init__(self, reply=None):
        self.acts = []
        self.reply = reply or {'ok': True, 'code': 'bug_saved', 'bug': 7, 'text': '收到了，修好会告诉你'}

    async def isle_act(self, world, ref=''):
        self.acts.append(world)
        return self.reply


async def no_sleep(_s):
    return None


def act(c, j, w):
    return asyncio.run(T.act(c, json.dumps(w, ensure_ascii=False), journal=j, sleep=no_sleep))


def test_report_what():
    assert T.report_what({'do': 'report', 'about': 'bug', 'why': '田那行写空着'}) == '报岛上的问题'
    assert T.report_what({'do': 'report', 'about': ' BUG ', 'text': 'x'}) == '报岛上的问题'
    assert T.report_what({'do': 'report', 'about': '报 问题', 'text': 'x'}) == '报岛上的问题'
    assert T.report_what({'do': 'report', 'about': '毛病', 'text': 'x'}) == '报岛上的问题'
    assert T.report_what({'do': 'report', 'shop': 'S-12', 'why': '没上菜'}) == '举报一家店'
    assert T.report_what({'do': 'report', 'about': '店', 'why': '没上菜'}) == '举报一家店'
    assert T.report_what({'do': 'walk', 'about': 'bug'}) == ''
    assert T.report_what(None) == ''


def test_bug_report_goes_to_the_island_and_the_diary_says_so():
    c, j = Client(), Journal()
    w = {'do': 'report', 'about': 'bug', 'why': '「我的」里田那行写空着，田里明明种着白菜'}
    assert '收到了，修好会告诉你' in act(c, j, w)
    assert c.acts == [w]
    assert '在岛上报岛上的问题：收到了，修好会告诉你' in j.j[0]


def test_shop_report_still_reads_jubao():
    c, j = Client({'ok': True, 'code': 'reported', 'text': '记下了'}), Journal()
    act(c, j, {'do': 'report', 'shop': 'S-12', 'why': '没上菜'})
    assert '举报一家店' in j.j[0] and '报岛上的问题' not in j.j[0]
