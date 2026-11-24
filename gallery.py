"""0.4.15 画廊上岛（协调 11-25；和 DSH 插件 0.9.19 lib/gallery.js 同一套；接口见共享笔记「画廊上岛-接口说明-1126.md」第四节、7.3）：
  - 试画先报价（岛上回 code: "quoted"，带报价号 G-…），插件记下报价号和时间；她写 {"do":"gallery_confirm"} 时插件核对主人在私聊里、
    报价以后亲口点头（和交楼、确认一样，只认他自己打的字），过了才调 isle/gallery/confirm，那时才扣鲸币。
  - 主人发的图当参考图：插件在内存里记下他最近发的图（AstrBot 的 Image 消息段，不存图）；她写 "ref":"主人的图" 时插件传上去，换成 u_ 号再交岛上。
  - 主人打分：照片洗好了（media 事件 photo_done）30 分钟内，主人一句「4 星」「⭐⭐⭐⭐」，插件替他打给小镇（photo/rate）。
小镇没开（404、画廊还没开）时不出声：她看到的只是「画廊还没开」，打分的话插件什么都不说。这里只有记账和认字，没有网络。"""
from __future__ import annotations

import re
import time

QUOTE_RE = re.compile(r'^G-[0-9a-f]{10}$', re.I)
QUOTE_FOR_S = 2 * 3600       # 小镇：报价过了 2 小时作废（old）
PIC_FOR_S = 30 * 60          # 主人发的图：30 分钟里能拿来当参考图
RATE_FOR_S = 30 * 60         # 照片洗好后 30 分钟里他说的「4 星」才算打分（和托管鱼的机器人同一个规矩）
UPLOAD_MAX = 8 * 1024 * 1024
KEEP = 5

# 试画的点头话：交楼那套（cobuild.is_yes）之外再认「画吧」「就画」「试一次」；「不画」「别画」不算（岛上会再用同一套规矩过一遍）
GALLERY_YES = re.compile(r'画吧|画了吧|就画|可以画|去画|开画|画一(张|幅|下)|那就画|试(一下|一次|吧)|试试|就试')
GALLERY_NO = re.compile(r'[不别]画|不用画|[不别]试|先别')

_ZH_NUM = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5}
_STAR_RE = re.compile(r'^(?:给你?|打)?\s*([1-5一二两三四五])\s*颗?\s*(?:星|分)\s*(?:吧|啦|了|哦|呀|[!！。.~～])*$')
_STAR_EMOJI_RE = re.compile(r'^[⭐★🌟]{1,5}$')


def is_owner_pic_ref(ref) -> bool:
    """她写的 ref 是不是「主人发的那张图」（u_ / r_ / al_ 开头的号不是）。"""
    t = str(ref if ref is not None else '').strip()
    if not t or re.match(r'^(u|r|al)_', t, re.I):
        return False
    return bool(re.match(r'^(主人|owner)', t, re.I) or re.match(r'^(他|你)(发|给)的?(那张)?(图|照片|图片)', t))


def stars_of(text) -> int:
    """主人这句话是不是给照片打分：「4 星」「4分」「四星」「给你 5 星」「⭐⭐⭐⭐」 -> 1–5；别的（「4 分钟到」「我给 4 星好评吗？」）-> 0。"""
    t = re.sub(r'\s+', ' ', str(text if text is not None else '')).strip()
    if not t or len(t) > 12:
        return 0
    bare = re.sub(r'\s', '', t)
    if _STAR_EMOJI_RE.match(bare):
        return len(bare)
    m = _STAR_RE.match(t)
    if not m:
        return 0
    n = _ZH_NUM.get(m.group(1)) or int(m.group(1))
    return n if 1 <= n <= 5 else 0


def image_type(data: bytes) -> str:
    """看字节认图的类型（AstrBot 的图不带类型）：jpg / png / webp，别的回 ''。"""
    b = bytes(data[:16]) if data else b''
    if b.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if b.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if b[:4] == b'RIFF' and b[8:12] == b'WEBP':
        return 'image/webp'
    return ''


class Gallery:
    """一条鱼的画廊账本：报价、最近洗好的照片存在本子里（journal.s['gallery']）；主人发的图只在内存里（插件重载后要他再发一次）。"""

    def __init__(self, journal, now=time.time):
        self.j, self.now = journal, now
        self.pics: dict = {}     # 私聊 -> (时间, [Image 消息段])

    def _g(self) -> dict:
        g = self.j.s.get('gallery')
        if not isinstance(g, dict):
            g = self.j.s['gallery'] = {}
        if not isinstance(g.get('quotes'), list):
            g['quotes'] = []
        if not isinstance(g.get('photos'), list):
            g['photos'] = []
        return g

    def _save(self):
        try:
            self.j.save()
        except Exception:  # noqa: BLE001 下次再存
            pass

    # --- 报价 -----------------------------------------------------------------------------------------------------------
    def quoted(self, r) -> dict | None:
        """岛上回了报价（code: "quoted"）：记下报价号、价、时间。别的回执不管。"""
        if not isinstance(r, dict) or r.get('code') != 'quoted' or not QUOTE_RE.match(str(r.get('quote') or '')):
            return None
        g = self._g()
        try:
            coin = float(r.get('coin') or 0)
        except (TypeError, ValueError):
            coin = 0
        q = {'quote': str(r['quote']), 'coin': int(coin) if coin == int(coin) else coin, 'at': self.now()}
        g['quotes'] = [*[o for o in g['quotes'] if o.get('quote') != q['quote']], q][-KEEP:]
        self._save()
        return q

    def quote(self, want: str = '') -> dict | None:
        """要确认的那个报价：写了号就找那个号，没写就是最近一个；过了 2 小时的不算。"""
        w = str(want or '').strip().lower()
        live = [o for o in self._g()['quotes'] if self.now() - float(o.get('at') or 0) < QUOTE_FOR_S]
        if w:
            return next((o for o in live if str(o.get('quote')).lower() == w), None)
        return live[-1] if live else None

    def confirmed(self, quote: str):
        g = self._g()
        g['quotes'] = [o for o in g['quotes'] if o.get('quote') != quote]
        self._save()

    # --- 主人发的图 -----------------------------------------------------------------------------------------------------
    def owner_pics(self, key, pics: list):
        k = str(key or '')
        if not k or not pics:
            return
        self.pics[k] = (self.now(), list(pics)[:4])
        for x in [x for x, (at, _) in self.pics.items() if self.now() - at > PIC_FOR_S]:
            self.pics.pop(x, None)

    def latest_pic(self, key):
        """这个私聊里主人 30 分钟内发的最后一张图（Image 消息段），没有就 None。"""
        got = self.pics.get(str(key or ''))
        if not got or self.now() - got[0] > PIC_FOR_S or not got[1]:
            return None
        return got[1][-1]

    # --- 照片和打分 -----------------------------------------------------------------------------------------------------
    def photo_done(self, order_id):
        oid = str(order_id or '')
        if not oid:
            return
        g = self._g()
        g['photos'] = [*[p for p in g['photos'] if p.get('order_id') != oid], {'order_id': oid, 'at': self.now()}][-KEEP:]
        self._save()

    def rate_target(self) -> str:
        p = [o for o in self._g()['photos'] if self.now() - float(o.get('at') or 0) < RATE_FOR_S]
        return p[-1]['order_id'] if p else ''

    def rating_for(self, text) -> dict | None:
        """主人这句话要不要替他打分：是「4 星」这类、30 分钟内有洗好的照片 -> {order_id, score}；不是 -> None。"""
        score = stars_of(text)
        oid = self.rate_target() if score else ''
        return {'order_id': oid, 'score': score} if oid else None

    def rated(self, order_id, score: int):
        for p in self._g()['photos']:
            if p.get('order_id') == order_id:
                p['stars'], p['rated_at'] = score, self.now()
                self._save()
                return


async def owner_said(gallery: Gallery, key: str, pics: list, text: str, client, log=None):
    """main.watch_owner：主人在私聊里亲手发的这一条。记下他发的图；照片洗好 30 分钟里一句「4 星」替他打给小镇。
    小镇还没开打分（404 之类）：不出声。回小镇的回执（测试用）或 None。"""
    if pics:
        gallery.owner_pics(key, pics)
    from .cobuild import owner_text          # 别的插件、平台加的时间行不是他打的字（0.4.12 那回事）
    rate = gallery.rating_for(owner_text(text))
    if not rate or client is None or not hasattr(client, 'photo_rate'):
        return None
    try:
        r = await client.photo_rate(rate['order_id'], rate['score'])
    except Exception as error:  # noqa: BLE001 小镇还没开打分就不管
        if log is not None:
            log.debug(f'[鱼缸小镇] 照片打分没交上：{error!r}')
        return None
    if isinstance(r, dict) and r.get('ok') is not False:
        gallery.rated(rate['order_id'], rate['score'])
        if log is not None:
            log.info(f"[鱼缸小镇] 主人给照片打了 {rate['score']} 星")
    return r
