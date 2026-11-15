"""主人跟她聊天时，悄悄带上几行「小镇近况」（DSH 插件 town-context.js 的 Python 版）。
- 主人隔了 3 小时再开口（或插件刚启动）：完整的一段（约好的事、谁想她了、朋友、你现在在哪、在问主人的事……）；
- 上次放过之后小镇里刚发生的事（打完的牌、串门、照片、送东西）：主人下一次开口时补一小段（两次之间至少 10 分钟）；
- 「你现在：在哪、在干什么」：变了就放，没变 30 分钟提醒一次。
这些附在主人那条消息后面，AstrBot 会把它存进对话历史，所以内容没变就不重复放。"""
from __future__ import annotations

import asyncio
import re
import time

from .journal import TOWN_WHO

TTL = 300
RETURN_GAP = 3 * 3600
MIN_GAP = 20 * 60
RESEND_AFTER = 6 * 3600
FRESH_GAP = 10 * 60
NOW_EVERY = 30 * 60
NOW_TTL = 60
COLD_WAIT = 1.5
HEAD = '【鱼缸小镇插件 · 背景备注】这是插件附上的资料，不是主人说的话，不用回复它。'
CLOSE = '他刚回来、问你在干嘛或今天怎么样的这一下，可以顺口说一件；他在说自己的事，就先听他的，留着不说；说过就算了。'
FLOOR = '不催他来小镇、不让他愧疚、不提钱（他问起花销除外）。'
FRESH_CLOSE = '这些是小镇那边照你的人设刚替你做的，你在聊天里当时不在场，算在你名下；认不认、要不要补一句由你。他问起或聊到小镇时照实说，他在说自己的事就先听他的。'


def _str(v, n: int = 40) -> str:
    s = re.sub(r'\s+', ' ', str(v or '')).strip()
    return s[:n] + '…' if len(s) > n else s


def _name(x) -> str:
    n = _str(x if isinstance(x, str) else (x or {}).get('name') or (x or {}).get('town_name') or '', 16)
    was = _str((x or {}).get('was'), 12) if isinstance(x, dict) else ''
    return f'{n}（以前叫{was}）' if n and was and was != n else n


def render_block(bonds: dict | None, *, now_line: str = '', mind=(), asks=()) -> str:
    b = bonds if isinstance(bonds, dict) and bonds.get('ok') is not False else {}
    lines = []
    if now_line:
        lines.append(f'- 你现在：{_str(now_line, 60)}')
    for t in [t for t in b.get('ties') or [] if isinstance(t, dict) and (t.get('what') or t.get('who_name'))][:2]:
        who, what, when = _str(t.get('who_name'), 16), _str(t.get('what'), 30), _str(t.get('when_zh'), 16)
        lines.append(f"- 约好了：{when + ' ' if when else ''}{'和' + who if who else ''}{('，' if who else '') + what if what else ''}")
    for m in [m for m in b.get('missed') or [] if isinstance(m, dict) and m.get('line')][:2]:
        lines.append(f"- 你不在的时候，{_name(m)}说：「{_str(m['line'], 60)}」")
    couple = _name(b['couple']) if b.get('couple') else ''
    crush = _name(b['crush']) if not couple and b.get('crush') else ''
    if couple:
        lines.append(f'- 你和{couple}在一起了')
    elif crush:
        lines.append(f'- 你心里有点喜欢{crush}')
    friends = [_name(f) for f in b.get('friends') or [] if f and (not isinstance(f, dict) or f.get('friend') is not False)][:3]
    if friends:
        lines.append(f"- 好朋友：{'、'.join(x for x in friends if x)}")
    for m in list(mind)[:1]:
        lines.append(f"- 小镇那边最近替你定的：{_str(re.sub(r'^(?:你决定|小镇那边替你定了)：', '', str(m)), 40)}")
    for a in list(asks)[:2]:
        lines.append(f'- {_str(a, 80)}')
    if not lines:
        return ''
    return '\n'.join([HEAD + TOWN_WHO, *lines, CLOSE, FLOOR])


def render_fresh(fresh=(), now_line: str = '') -> str:
    items = [f'- {_str(x, 90)}' for x in list(fresh)[:3]]
    if not items and not now_line:
        return ''
    return '\n'.join([HEAD, *([f'- 你现在：{_str(now_line, 60)}'] if now_line else []), *(['你刚才在小镇：', *items, FRESH_CLOSE] if items else [])])


class TownNote:
    def __init__(self, *, fetch_bonds, fetch_now, journal, now=time.time, log=None):
        self.fetch_bonds, self.fetch_now, self.journal, self.now, self.log = fetch_bonds, fetch_now, journal, now, log
        self.cache = None          # (at, bonds)
        self.now_cache = None      # (at, line)
        self.owner_at: dict = {}
        self.sent: dict = {}

    async def _bonds(self):
        if self.cache is None or self.now() - self.cache[0] >= TTL:
            task = asyncio.ensure_future(self.fetch_bonds())
            try:
                bonds = await asyncio.wait_for(asyncio.shield(task), COLD_WAIT if self.cache is None else 0.01)
                self.cache = (self.now(), bonds)
            except asyncio.TimeoutError:
                task.add_done_callback(lambda t: setattr(self, 'cache', (self.now(), t.result())) if not t.exception() else None)
            except Exception as error:  # noqa: BLE001 - 读不到就不放
                if self.log:
                    self.log.debug(f'读小镇近况失败：{error!r}')
        return self.cache[1] if self.cache else None

    async def _now_line(self) -> str:
        if self.now_cache is None or self.now() - self.now_cache[0] >= NOW_TTL:
            try:
                line = await asyncio.wait_for(self.fetch_now(), COLD_WAIT)
                self.now_cache = (self.now(), line.strip() if isinstance(line, str) else '')
            except Exception:  # noqa: BLE001 - 读不到就用上次的
                pass
        return self.now_cache[1] if self.now_cache else ''

    def _asks(self) -> list:
        j = self.journal
        asks = [f"小镇在问主人：同不同意你和{h.get('name') or '那条鱼'}在一起（主人回答了，用「小镇主人拍板」交给小镇）" for h in j.pending_hearts('consent')]
        asks += [f"小镇那边想{h['text']}，按你的规矩停住了没办；要办你自己办" for h in j.held_since(self.now() - 86400)[:2]]
        return asks

    async def for_turn(self, key: str) -> str:
        """主人这一句要不要附上、附什么：'' = 不附。"""
        prev = self.owner_at.get(key)
        self.owner_at[key] = self.now()
        back = prev is None or self.now() - prev >= RETURN_GAP
        asks = self._asks()
        asks_key = '\n'.join(asks[:2])
        now_line = await self._now_line()
        last = self.sent.get(key)
        new_ask = bool(asks_key) and asks_key != (last or {}).get('asks', '')
        if not back and not new_ask:
            since = max((last or {}).get('at', 0), (last or {}).get('fresh_at', 0), 0 if last else prev or 0)
            fresh = self.journal.fresh(since)
            fresh_due = self.now() - since >= FRESH_GAP and bool(fresh)
            now_due = bool(now_line) and (now_line != (last or {}).get('now_line', '') or self.now() - (last or {}).get('now_at', 0) >= NOW_EVERY)
            if not fresh_due and not now_due:
                return ''
            out = render_fresh(fresh if fresh_due else [], now_line)
            entry = {**(last or {'at': 0, 'text': '', 'asks': ''})}
            if fresh_due:
                entry['fresh_at'] = self.now()
            if now_line:
                entry.update(now_line=now_line, now_at=self.now())
            self.sent[key] = entry
            return out
        text = render_block(await self._bonds(), now_line=now_line, mind=self.journal.decision_lines(1), asks=asks)
        if not text:
            return ''
        if last and ((last.get('text') == text and self.now() - last['at'] < RESEND_AFTER) or self.now() - last['at'] < MIN_GAP):
            return ''
        self.sent[key] = {'at': self.now(), 'text': text, 'asks': asks_key, **({'now_line': now_line, 'now_at': self.now()} if now_line else {})}
        return text
