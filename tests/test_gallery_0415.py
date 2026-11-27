"""0.4.15 画廊上岛（协调 11-25；共享笔记「画廊上岛-接口说明-1126.md」第四节、7.3；和 DSH 0.9.19 test/gallery-0919.test.mjs 同一套用例）：
试画报价 -> 主人在私聊里报价以后亲口点头 -> 插件确认；主人发的图当参考图；主人一句「4 星」打分；小镇没开时不出声。
（照方子拍照 7.2 只有 DSH 插件有：AstrBot 这边没有拍照工具。）"""
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town import isle_tools  # noqa: E402
from astrbot_plugin_fishbowl_town.cobuild import Cobuild, OwnerWords, is_yes_to  # noqa: E402
from astrbot_plugin_fishbowl_town.gallery import (GALLERY_NO, GALLERY_YES, PIC_FOR_S, QUOTE_FOR_S, RATE_FOR_S, Gallery,  # noqa: E402
                                                  image_type, is_owner_pic_ref, owner_said, stars_of)
from astrbot_plugin_fishbowl_town.journal import Journal  # noqa: E402
from astrbot_plugin_fishbowl_town.town_client import TownClient, TownError  # noqa: E402


def body(s) -> str:
    """0.4.17：结果最后一行是回执，比对的是前面的话。"""
    return re.sub(r'\n回执 \{[^\n]*\}$', '', str(s))

Q = 'G-0123456789'
QUOTE_R = {'ok': True, 'code': 'quoted', 'quote': Q, 'coin': 63, 'text': '照「海边」试一次：63 鲸币，主人点头后扣。'}
PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 992


class World:
    def __init__(self, tmp_path):
        self.t = [1_800_000_000.0]
        now = lambda: self.t[0]  # noqa: E731
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.journal = Journal(tmp_path / 'j.json', now=now)
        self.gallery = Gallery(self.journal, now=now)
        self.words = OwnerWords(now=now)
        self.calls = []
        w = self

        class Client:
            async def isle_act(self, world, ref=''):
                w.calls.append(('act', world, ref))
                return QUOTE_R if world.get('try') else {'ok': True, 'text': '收下了参考图：r_abc123'}

            async def gallery_confirm(self, quote, owner_words):
                w.calls.append(('confirm', quote, owner_words))
                return {'ok': True, 'code': 'making', 'order': 'go_1', 'paid': 63, 'text': '开画了'}

            async def gallery_upload(self, data, ctype):
                w.calls.append(('upload', len(data), ctype))
                return {'ok': True, 'upload_id': 'u_77'}
        self.client = Client()

        # 真的 cobuild._consent（主人私聊、报价以后、他亲手打的、像点头），只是不建整个 Cobuild
        class Fake:
            owner_words = self.words

            @staticmethod
            def is_owner_chat(key):
                return key == 'main'
        self.fake = Fake()
        self.pic = None                 # owner_pic() 读出来的 (字节, 类型)；'boom' = 读不出来

    def step(self, s):
        self.t[0] += s

    def consent(self, key='main'):
        return lambda since: Cobuild._consent(self.fake, key, since, '试画', '「画吧」', GALLERY_YES, GALLERY_NO)

    async def owner_pic(self):
        if self.pic == 'boom':
            raise OSError('expired')
        return self.pic

    def act(self, world, key='main'):
        return asyncio.run(isle_tools.act(self.client, world, '', journal=self.journal, sleep=lambda s: asyncio.sleep(0),
                                          gallery=self.gallery, consent=self.consent(key), owner_pic=self.owner_pic))


def test_0415_gallery_owner_words_and_star_readers():
    for t, n in [('4 星', 4), ('4分', 4), ('四星', 4), ('给你 5 星', 5), ('打3分吧', 3), ('⭐⭐⭐⭐', 4), ('★★', 2), ('5星！', 5)]:
        assert stars_of(t) == n, t
    for t in ['4 分钟到', '我给 4 星好评吗？', '6 星', '今天打了 4 分的游戏', '', '星星好看', None]:
        assert stars_of(t) == 0, t
    for t in ['画吧', '就画吧', '可以，画吧', '好', '试一次']:
        assert is_yes_to(t, GALLERY_YES, GALLERY_NO), t
    for t in ['不画了吧', '别画', '画吧？', '贵了', '多少钱', '先别']:
        assert not is_yes_to(t, GALLERY_YES, GALLERY_NO), t
    for r in ['主人的图', '主人发的图', 'owner', '他发的图']:
        assert is_owner_pic_ref(r), r
    for r in ['u_77', 'r_abc', 'al_9', '', None, '买家', '上一步']:
        assert not is_owner_pic_ref(r), r
    assert image_type(PNG) == 'image/png' and image_type(b'\xff\xd8\xff\xe0') == 'image/jpeg'
    assert image_type(b'RIFF\0\0\0\0WEBPVP8 ') == 'image/webp' and image_type(b'GIF89a') == '' and image_type(b'') == ''


def test_0415_gallery_ledger_quotes_2h_pictures_30min_rating_within_30min(tmp_path):
    w = World(tmp_path)
    g = w.gallery
    assert g.quoted({'ok': True, 'code': 'done'}) is None and g.quote() is None
    g.quoted(QUOTE_R)
    assert g.quote()['quote'] == Q and g.quote(Q.lower())['coin'] == 63 and g.quote('G-ffffffffff') is None
    assert json.loads((tmp_path / 'j.json').read_text(encoding='utf-8'))['gallery']['quotes'][0]['quote'] == Q   # 报价存在本子里，重载还在
    w.step(QUOTE_FOR_S + 1)
    assert g.quote() is None
    g.owner_pics('main', ['a1', 'a2'])
    assert g.latest_pic('main') == 'a2' and g.latest_pic('other') is None
    w.step(PIC_FOR_S + 1)
    assert g.latest_pic('main') is None
    assert g.rating_for('4 星') is None
    g.photo_done('sm_1')
    w.step(60)
    g.photo_done('sm_2')
    assert g.rating_for('4 星') == {'order_id': 'sm_2', 'score': 4}
    assert g.rating_for('好看') is None
    w.step(RATE_FOR_S + 1)
    assert g.rating_for('5 星') is None


def test_0415_gallery_try_keeps_the_quote_confirm_only_after_the_owner_nods_in_his_chat(tmp_path):
    w = World(tmp_path)
    r1 = w.act({'do': 'gallery', 'recipe': 'R-0123456789', 'try': 1, 'wish': '海边'})
    assert '只报了价，还没扣：报价号 G-0123456789，63 鲸币' in r1
    assert '{"do":"gallery_confirm","quote":"G-0123456789"}' in r1
    assert w.gallery.quote()['quote'] == Q
    # nothing from the owner yet
    assert '还没在这里亲口说话' in w.act({'do': 'gallery_confirm', 'quote': Q})
    # he says no
    w.step(1)
    w.words.observe('main', '不画了吧')
    assert '不像让你确认' in w.act({'do': 'gallery_confirm', 'quote': Q})
    # not the owner chat
    w.words.observe('qq', '画吧')
    assert '这个会话不算' in w.act({'do': 'gallery_confirm', 'quote': Q}, key='qq')
    assert not [c for c in w.calls if c[0] == 'confirm'], 'nothing sent to the island yet'
    # he nods: confirmed with his words (no quote written -> the latest one)
    w.step(1)
    w.words.observe('main', '可以，画吧')
    r = w.act({'do': 'gallery_confirm'})
    assert [c for c in w.calls if c[0] == 'confirm'] == [('confirm', Q, '可以，画吧')]
    assert '确认了（主人说「可以，画吧」，扣了 63 鲸币）' in r
    assert w.gallery.quote() is None, 'a confirmed quote no longer waits'
    assert any('确认了试画' in json.dumps(e, ensure_ascii=False) for e in w.journal.s.get('events', [])) or '确认了试画' in json.dumps(w.journal.s, ensure_ascii=False)
    # gallery_confirm never goes through isle/act
    assert not [c for c in w.calls if c[0] == 'act' and c[1].get('do') == 'gallery_confirm']


def test_0415_gallery_words_before_the_quote_do_not_count_refusals_and_closed_read_plainly(tmp_path):
    w = World(tmp_path)
    w.words.observe('main', '画吧')
    w.step(1)
    w.act({'do': 'gallery', 'recipe': 'R-0123456789', 'try': 1})
    assert '还没在这里亲口说话' in w.act({'do': 'gallery_confirm', 'quote': Q})
    w.step(1)
    w.words.observe('main', '画吧')

    async def gone(quote, owner_words):
        raise TownError('HTTP_404', 404)
    w.client.gallery_confirm = gone
    assert w.act({'do': 'gallery_confirm', 'quote': Q}) == isle_tools.GALLERY_CLOSED

    async def off(quote, owner_words):
        return {'ok': False, 'code': 'gallery_off', 'text': '画廊还没开（镇上开了会说）。什么都没办、没扣'}
    w.client.gallery_confirm = off
    assert w.act({'do': 'gallery_confirm', 'quote': Q}) == isle_tools.GALLERY_CLOSED

    async def closed(quote, owner_words):
        return {'ok': False, 'code': 'closed', 'text': '这家画廊关了：什么都没扣'}
    w.client.gallery_confirm = closed
    r = w.act({'do': 'gallery_confirm', 'quote': Q})
    assert '这家画廊关了' in r and '没确认（closed）' in r
    assert w.gallery.quote()['quote'] == Q, 'still waiting after 404 / off / closed'

    async def old(quote, owner_words):
        return {'ok': False, 'code': 'old', 'text': '这个报价过期了'}
    w.client.gallery_confirm = old
    r = w.act({'do': 'gallery_confirm', 'quote': Q})
    assert '没确认（old）。报价过期了' in r and '什么都没扣' in r
    assert w.gallery.quote() is None, 'the island said old: no longer waited for'
    # no quote at all
    v = World(tmp_path / 'v')
    assert '现在没有在等主人点头的试画报价' in v.act({'do': 'gallery_confirm'})
    assert '没有在等的报价 G-aaaaaaaaaa' in v.act({'do': 'gallery_confirm', 'quote': 'G-aaaaaaaaaa'})


def test_0415_gallery_owner_picture_uploads_his_latest_and_sends_the_u_number(tmp_path):
    w = World(tmp_path)
    assert '主人最近 30 分钟没在这里发图' in w.act({'do': 'gallery', 'spot': 'S-1', 'ref': '主人的图'})
    assert w.calls == []
    w.pic = (PNG, image_type(PNG))
    r = w.act({'do': 'gallery', 'spot': 'S-1', 'ref': '主人的图'})
    assert [c[0] for c in w.calls] == ['upload', 'act']
    assert w.calls[0] == ('upload', 1000, 'image/png')
    assert w.calls[1][1] == {'do': 'gallery', 'spot': 'S-1', 'ref': 'u_77'}
    assert 'r_abc123' in r
    # the island already has a number: passed as written, no upload
    w.calls.clear()
    w.act({'do': 'gallery', 'spot': 'S-1', 'ref': 'al_5'})
    assert [c[0] for c in w.calls] == ['act']
    # too big / no type known -> jpeg / closed / busy / unreadable
    w.calls.clear()
    w.pic = (b'\0' * (8 * 1024 * 1024 + 1), '')
    assert '超过 8 MB' in w.act({'do': 'gallery', 'spot': 'S-1', 'ref': '主人的图'}) and w.calls == []
    w.pic = (b'\0' * 10, '')
    w.act({'do': 'gallery', 'spot': 'S-1', 'ref': '主人的图'})
    assert w.calls[0] == ('upload', 10, 'image/jpeg')
    w.calls.clear()

    async def gone(data, ctype):
        raise TownError('HTTP_404', 404)
    w.client.gallery_upload = gone
    assert body(w.act({'do': 'gallery', 'spot': 'S-1', 'ref': '主人的图'})) == isle_tools.GALLERY_CLOSED
    assert not [c for c in w.calls if c[0] == 'act']

    async def busy(data, ctype):
        return {'ok': False, 'code': 'busy', 'text': '太多了'}
    w.client.gallery_upload = busy
    assert '12 张' in w.act({'do': 'gallery', 'spot': 'S-1', 'ref': '主人的图'})
    w.pic = 'boom'
    assert '读不出来' in w.act({'do': 'gallery', 'spot': 'S-1', 'ref': '主人的图'})


def test_0415_main_tool_description_carries_the_gallery_way_and_wires_the_ledger():
    src = (ROOT / 'main.py').read_text(encoding='utf-8')
    assert isle_tools.GALLERY_HOW in src
    assert 'verb 填「画廊」' in isle_tools.GALLERY_HOW
    assert 'gallery=self.gallery, consent=consent, owner_pic=owner_pic' in src
    assert "self.cobuild.consent(umo, since, '试画', '「画吧」', GALLERY_YES, GALLERY_NO)" in src
    assert 'on_event=self._on_event' in src and "d.get('what') == 'photo_done'" in src
    # same words as the DSH plugin
    dsh = Path('F:/Olivia/ClaudeWork/wt-dsh-0919/lib/isle-tools.js')
    if dsh.exists():
        assert isle_tools.GALLERY_HOW in dsh.read_text(encoding='utf-8')


class _Res:
    def __init__(self, status, body):
        self.status, self._t, self.headers = status, json.dumps(body), {}

    async def text(self):
        return self._t

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Session:
    closed = False

    def __init__(self):
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        if url.endswith('/photo/rate'):
            return _Res(404, {'error': 'HTTP_404'})
        if url.endswith('/isle/gallery/upload'):
            return _Res(200, {'ok': True, 'upload_id': 'u_1'})
        return _Res(200, {'ok': True, 'order_id': 'sm_9'})


def test_0415_client_photo_rate_404_never_shuts_photo_upload_sends_raw_bytes():
    s = _Session()
    c = TownClient('fbo_testcode123', session=s)

    async def run():
        try:
            await c.photo_rate('sm_1', 4)
            raise AssertionError('404 expected')
        except TownError as error:
            assert error.status == 404
        assert not c.shut, 'photo/rate is core: a 404 shuts nothing'
        got = await c.gallery_upload(b'\x01\x02\x03', 'image/webp')
        assert got['upload_id'] == 'u_1'
    asyncio.run(run())
    rate = next(x for x in s.calls if x[1].endswith('/photo/rate'))
    assert json.loads(rate[2]['data']) == {'order_id': 'sm_1', 'score': 4}
    up = next(x for x in s.calls if x[1].endswith('/isle/gallery/upload'))
    assert up[2]['headers']['Content-Type'] == 'image/webp' and up[2]['data'] == b'\x01\x02\x03'


def test_0415_owner_said_rates_once_within_30min_of_photo_done_and_a_404_is_quiet(tmp_path):
    w = World(tmp_path)
    g = w.gallery
    sent = []
    reply = [{'ok': True, 'score': 4}]

    class C:
        async def photo_rate(self, order_id, score):
            sent.append((order_id, score))
            if isinstance(reply[0], Exception):
                raise reply[0]
            return reply[0]
    c = C()
    run = lambda text, pics=(): asyncio.run(owner_said(g, 'main', list(pics), text, c))  # noqa: E731
    assert run('4 星') is None and sent == [], 'no photo yet: nothing sent'
    run('看这张', ['img1'])
    assert g.latest_pic('main') == 'img1'
    g.photo_done('sm_5')
    w.step(5 * 60)
    run('[2026-11-26 12:00:01]\n4 星')                 # a platform time line is not his words
    assert sent == [('sm_5', 4)]
    assert g._g()['photos'][-1]['stars'] == 4
    assert run('这张不错') is None and len(sent) == 1
    reply[0] = TownError('HTTP_404', 404)
    assert run('5 星') is None, 'the town has no rating yet: quiet'
    w.step(RATE_FOR_S)
    assert run('3 星') is None and len(sent) == 2, 'too late'
    # no client (not on duty): pictures still noted, nothing sent
    asyncio.run(owner_said(g, 'main', ['img2'], '4 星', None))
    assert g.latest_pic('main') == 'img2'
