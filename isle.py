"""鱼缸岛的回合（DSH 插件 lib/isle.js 的 Python 版；接口见小镇 platform/岛-回合瘦身-接口.md）。
主人 11-20：「岛的规模人数会越来越大，发整个回合过去负担太大了」→「可以，做」。
1. 规矩只发一次：回合里只带版本号 packet.isle.static；规矩全文插件按版本存一份（town.json 的 isle_static），
   拼提示时放在最前面——AstrBot 每一问都是新的一问，人设 + 规矩每次都一样，她那边模型的前缀缓存才命中得了。版本变了才再取。
2. 想看再查：她的回答写 {"world":{"do":"look","topic":…}}，插件向小镇要这一页、递回给她，最多 3 次，然后她出手。查看不占她的行动次数。
岛上拿主意的回合时限是 packet.isle.within_s（120 秒），不是牌桌的 25 秒。
3. 看图（主人 11-20「直接弄吧，很便宜无所谓」；小镇 platform/岛-鱼看图-接口.md）：查到的那页带 pics（地图、那个地方的样子）时，
   她的模型看得了图就把图附在递回去的那一问里，看不了就只给图上画的是什么（alt）。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

READ_MAX = 3
READ_TEXT_MAX = 1500
# 0.4.4「怎么做」一次给全（建模说明约 3,000 字、写程序约 5,000 字，小镇不分页）：按 1500 截掉，她就看不到上限和价钱
HOWTO_TEXT_MAX = 6000
WITHIN_MAX_S = 180
PIC_MAX = 2          # 一页最多带几张图
ALT_MAX = 120

BLOCKS = (('rules', '岛上的规矩'), ('verbs', '能做的事（细节用 look 查「怎么做」）'), ('goods', '标准货品'), ('law', '镇规'))
TOPIC_ZH = {'地': '挂着的地', '楼': '待承包的楼', '铺位': '空铺位', '店': '开着的店', '订单': '接力订单', '缺的': '最缺的',
            '统计': '镇上统计', '镇规': '镇规', '怎么做': '怎么做', '我的店': '我的店', '价': '价钱', '地图': '地图', '样子': '那儿的样子',
            '田': '田', '建模': '建模说明'}
_BUILD_VERB = re.compile(r'建模|build_guide|盖房')


def text_max(read) -> int:
    return HOWTO_TEXT_MAX if isinstance(read, dict) and read.get('topic') in ('怎么做', '建模') else READ_TEXT_MAX
CIRCLED = '①②'
_HEX = re.compile(r'^[0-9a-fA-F]{6,64}$')


def _isle(turn) -> dict:
    p = (turn or {}).get('packet') if isinstance(turn, dict) else None
    i = p.get('isle') if isinstance(p, dict) else None
    return i if isinstance(i, dict) else {}


def isle_version_of(turn) -> str:
    """回合里的规矩版本号（没有 = 不是岛上的回合，或旧小镇）。"""
    v = _isle(turn).get('static')
    return v if isinstance(v, str) and _HEX.match(v) else ''


def isle_within_s(turn) -> float:
    """岛上回合的时限（秒）；不是岛上的回合回 0（用插件原来的 25 秒）。"""
    try:
        s = float(_isle(turn).get('within_s') or 0)
    except (TypeError, ValueError):
        return 0
    return min(WITHIN_MAX_S, s) if s > 25 else 0


def rules_text(blocks) -> str:
    """小镇给的几块规矩 -> 一段字（按固定顺序，同一个版本拼出来永远一样）。"""
    if not isinstance(blocks, dict):
        return ''
    out = []
    for k, title in BLOCKS:
        v = blocks.get(k)
        text = '\n'.join(str(x) for x in v) if isinstance(v, list) else v if isinstance(v, str) else ''
        if text.strip():
            out += [f'〔{title}〕', text.strip()]
    return '\n'.join(out)


def read_of(answer):
    """她的回答里要查的那一下（{"world":{"do":"look",…}}）；不是查看回 None。
    0.4.4：{"world":{"do":"build_guide","lot":…}}（建模说明，只读）也当一次查看：这一回合里就查回来给她，不占她拿主意的这一步。"""
    try:
        a = json.loads(answer) if isinstance(answer, str) else answer
    except ValueError:
        return None
    w = a.get('world') if isinstance(a, dict) else None
    if not isinstance(w, dict) or w.get('do') not in ('look', 'build_guide'):
        return None
    out = {k: (v[:60] if isinstance(v, str) else v) for k, v in w.items() if isinstance(v, (str, int, float, bool))}
    if w.get('do') == 'build_guide':
        out.update(do='look', topic='怎么做', verb='建模')
    return out


def drop_look(answer: str) -> str:
    """到头了她还在查：把 look 去掉，回答里别的（action、plan、note）照交；只剩 look 就交「先不动」。"""
    try:
        a = json.loads(answer)
    except (TypeError, ValueError):
        return answer
    if not isinstance(a, dict):
        return answer
    a.pop('world', None)
    if not a:
        return json.dumps({'action': 'none', 'note': '还在看岛上的情况，下次再定'}, ensure_ascii=False)
    return json.dumps(a, ensure_ascii=False)


def clip(s, n: int) -> str:
    t = str(s if s is not None else '')
    return t[:n - 1] + '…' if len(t) > n else t


def pics_of(r) -> list:
    """查到的那页带的图：[{url, alt}]，最多 PIC_MAX 张；没有或格式不对回 []。"""
    raw = r.get('pics') if isinstance(r, dict) else None
    out = []
    for p in raw if isinstance(raw, list) else []:
        if isinstance(p, dict) and isinstance(p.get('url'), str) and p['url'].strip():
            out.append({'url': p['url'].strip(), 'alt': clip(' '.join(str(p.get('alt') or '').split()), ALT_MAX)})
        if len(out) >= PIC_MAX:
            break
    return out


NO_PICS_HEAD = '【这一页带了图，你这边看不了图；图上画的是】'
_PICS_HEAD = re.compile(r'【这一页带了 \d 张图，就附在这条消息里，照图上看到的拿主意】')


class PicsRefused(Exception):
    """她的模型这一问不收图（main._ask 抛）：把「附在这条消息里」改成只给字，再问一次。"""


def pic_note(pics: list, attached: bool) -> str:
    """接在查到的那页字后面：附了图就说附在这一问里；看不了图就只说图上画的是什么。"""
    if not pics:
        return ''
    alts = [f'{CIRCLED[k]} {p["alt"] or "（没写画的是什么）"}' for k, p in enumerate(pics)]
    head = f'【这一页带了 {len(pics)} 张图，就附在这条消息里，照图上看到的拿主意】' if attached else NO_PICS_HEAD
    return '\n'.join(['', head, *alts])


def unattach(text: str) -> str:
    """图没递成：字里「附在这条消息里」改成「你这边看不了图」，后面几问里就不会说着一张不存在的图。"""
    return _PICS_HEAD.sub(NO_PICS_HEAD, text)


def build_read_followup(read: dict, text: str, left: int) -> str:
    """查到的那一页递回给她的话。"""
    topic = (TOPIC_ZH['建模'] if read.get('topic') == '怎么做' and _BUILD_VERB.search(str(read.get('verb') or ''))
             else TOPIC_ZH.get(read.get('topic')) or read.get('topic') or '岛上')
    tail = (f'接着拿主意：照原来的要求只输出那段 JSON。还想看别的可以再写一次 look（这一回合还能查 {left} 次），想好了就直接写你要做的事。'
            if left > 0 else '这一回合查不了了：现在就照原来的要求只输出那段 JSON，写你要做的事（不再写 look）。')
    return '\n'.join([f'【你刚查的：{topic}】', text or '（这一页是空的）', '', tail])


class Isle:
    def __init__(self, client, journal, log=None):
        self.client, self.journal = client, journal
        self.log = log or logging.getLogger('fishbowl_town')
        self._fetching: asyncio.Future | None = None

    def _have(self) -> dict:
        h = self.journal.s.get('isle_static')
        return h if isinstance(h, dict) else {}

    async def prefix(self, turn) -> str:
        """拼提示时放在最前面的规矩全文；取不到就用手上旧的那份，再没有就是空的（小镇回合里的「查」那一行还在）。"""
        want = isle_version_of(turn)
        if not want or not hasattr(self.client, 'isle_static'):
            return ''
        have = self._have()
        if have.get('version') == want and have.get('text'):
            return have['text']
        if self._fetching is None or self._fetching.done():
            self._fetching = asyncio.ensure_future(self._fetch(have))
        return await asyncio.shield(self._fetching)

    async def _fetch(self, have: dict) -> str:
        try:
            r = await self.client.isle_static(have.get('version') or '')
            if isinstance(r, dict) and r.get('same') and have.get('text') and r.get('version') == have.get('version'):
                return have['text']
            text = rules_text(r.get('blocks')) if isinstance(r, dict) and isinstance(r.get('version'), str) else ''
            if text:
                self.journal.s['isle_static'] = {'version': r['version'], 'text': text, 'at': time.time()}
                self.journal.save()
                self.log.info(f"鱼缸岛的规矩换到第 {r['version']} 版（{len(text)} 字）")
                return text
        except Exception as error:  # noqa: BLE001 - 取不到就用手上的
            self.log.info(f'鱼缸岛的规矩这次没取到（{getattr(error, "code", None) or error}），先用手上的')
        return have.get('text') or ''

    async def read_loop(self, turn, answer: str, *, ask, to_answer, left, min_s: float = 8, max_reads: int = READ_MAX, see=None) -> dict:
        """她要先查：向小镇要那一页、递回给她，最多 max_reads 次；时间不够就不查了，请她直接定。
        ask(text[, images]) -> 她的原话（异步，出错回 ''）；to_answer(raw) -> 交给小镇的字符串或 None；left() -> 还剩几秒。
        see(pics) -> 能附给她的图（base64:// 串的列表；她的模型看不了图、取图失败回 []）。"""
        cur, reads = answer, 0
        while True:
            read = read_of(cur) if cur is not None else None
            if not read:
                return {'answer': cur, 'reads': reads}
            if reads >= max_reads or left() < min_s or not hasattr(self.client, 'isle_read'):
                if left() >= min_s:
                    again = to_answer(await ask(build_read_followup(read, '（这一回合查不了了）', 0)))
                    if again is not None and not read_of(again):
                        return {'answer': again, 'reads': reads}
                return {'answer': drop_look(cur), 'reads': reads, 'cut': True}   # 还是在查：look 不交给小镇，别的照交
            reads += 1
            pics, images = [], []
            try:
                r = await self.client.isle_read(turn['turn_id'], read)
                if isinstance(r, dict) and r.get('ok') is not False:
                    text = clip(r.get('text'), text_max(read)) + (f'\n（还有下一页：再查一次，写 "page": "{clip(r.get("next"), 40)}"）' if r.get('next') else '')
                    pics = pics_of(r)
                else:
                    text = f"这次没查到：{clip((r or {}).get('text') if isinstance(r, dict) else '', 120) or '小镇没说为什么'}。就按你手上的情况拿主意。"
            except Exception as error:  # noqa: BLE001 - 查不到就按手上的定
                text = f"这次没查到（{getattr(error, 'code', None) or '出错了'}）。就按你手上的情况拿主意。"
            if pics and see is not None:
                try:
                    images = list(await see(pics) or [])[:PIC_MAX]
                except Exception as error:  # noqa: BLE001 - 图取不到：只给字
                    self.log.info(f'查到的图这次没取到（{getattr(error, "code", None) or error}），只给她图上画的是什么')
            follow = build_read_followup(read, text + pic_note(pics, bool(images)), max_reads - reads)
            nxt = to_answer(await (ask(follow, images) if images else ask(follow)))
            if nxt is None:   # 看完那一下没答上（超时、读不了）：用她上一次的回答，去掉 look
                return {'answer': drop_look(cur), 'reads': reads, 'cut': True}
            cur = nxt
