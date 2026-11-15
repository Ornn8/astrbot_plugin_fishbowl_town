"""核心循环（DSH 插件 runner.js 的 Python 版）：长轮询小镇的回合 -> 交给她的模型回答 -> 时限内交回；每分钟读一次事件。
不认识 AstrBot：她的模型（ask）、告诉主人（tell / notify）都是传进来的，测试里换成假的。"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from . import events as ev
from .prompts import build_correction, build_hold_correction, build_turn_prompt, hold_check, to_answer
from .town_client import TownError, transient

POLL_WAIT = 25
MARGIN = 1.5          # 交回去之前留的余量（网络来回），秒
MIN_BUDGET = 3        # 剩下不到这么多就不问了
RETRY_MIN = 8         # 第一次答得不能读时，剩下的时间够这么多才追问一次
ANSWER_WITHIN = 23    # 本地上限：收到回合后这么久还没答完就放弃（小镇等 25 秒）
EVENTS_EVERY = 60


def again_prompt(prompt: str, said: str, correction: str) -> str:
    """每次调模型都是新的一问：追问时把她刚才的回答一起带上。"""
    return f"{prompt}\n\n【你刚才的回答】\n{str(said or '').strip()[:2000]}\n\n{correction}"


class Runner:
    def __init__(self, client, journal, ask, *, tell=None, notify=None, quiet=lambda: None, her_name=lambda: '', log=None, now=time.time):
        self.client, self.journal, self.ask = client, journal, ask
        self.tell, self.notify = tell, notify
        self.quiet, self.her_name = quiet, her_name
        self.log = log or logging.getLogger('fishbowl_town')
        self.now = now
        self.seen: dict = {}
        self.stats = {'turns': 0, 'answered': 0, 'invalid': 0, 'late': 0, 'closed': 0, 'errors': 0, 'events': 0, 'held': 0,
                      'connected': False, 'problem': '', 'last_turn_at': 0}
        self.running = False
        self._tasks: list = []
        self._auth_told = 0

    # --- 一个回合 -------------------------------------------------------------------------------------------------------
    def left(self, turn: dict, received: float) -> float:
        """还剩几秒能答。deadline 是小镇的绝对时间；电脑时钟可能不准，再用「收到以后过了多久」兜底。"""
        by_receipt = POLL_WAIT - (self.now() - received)
        try:
            by_deadline = float(turn.get('deadline') or 0) - self.now() if turn.get('deadline') else float('inf')
        except (TypeError, ValueError):
            by_deadline = float('inf')
        left = by_receipt if not -60 < by_deadline < 120 else min(by_deadline, by_receipt)
        return min(left, ANSWER_WITHIN - (self.now() - received) + MARGIN) - MARGIN

    async def _ask(self, prompt: str, timeout: float) -> str:
        return await asyncio.wait_for(self.ask(prompt), timeout=max(1.0, timeout))

    async def handle(self, turn: dict, received: float):
        self.stats['turns'] += 1
        tid, kind = turn['turn_id'], turn.get('kind')
        try:
            if self.left(turn, received) < MIN_BUDGET:
                self.stats['late'] += 1
                return
            j = self.journal
            if kind == 'agency':
                s = j.skip(turn, self.quiet(), j.pending_wish())
                if s:   # 她说了要歇着、勿扰时段、没什么可做的：插件替她答「先不动」，不调模型
                    await self._post(tid, json.dumps({'action': 'none', 'next_wake': s['minutes']}), turn, received)
                    self.log.info(f"拿主意的回合 {tid} 没叫她（{s['why']}）")
                    return
            rules = {'text': j.rules(), 'hold': j.holds()}
            mind = {'rules': rules}
            if kind == 'agency':
                mind.update(recent=j.recent_for_agency(), wish=j.pending_wish(), wish_own=j.wish_is_own())
            if kind == 'visit':
                plan = (j.pending_wish() if j.wish_is_own() else '') or j.doing()
                if plan:
                    mind['plan'] = plan
            prompt = build_turn_prompt(turn, seconds_left=int(self.left(turn, received)), **mind)
            raw = await self._ask(prompt, self.left(turn, received))
            answer = to_answer(turn, raw)
            if answer is None and self.left(turn, received) >= RETRY_MIN:
                raw = await self._ask(again_prompt(prompt, raw, build_correction(turn)), self.left(turn, received))
                answer = to_answer(turn, raw)
            # 她要先停住的事（请客送礼 / 答应约定）：请那边重答一次，还写着就插件拿掉；停住的事记给和主人聊天的她
            if answer is not None and rules['hold']:
                found, clean = hold_check(kind, answer, rules['hold'])
                if found:
                    held = list(found)
                    if self.left(turn, received) >= RETRY_MIN:
                        try:
                            again = to_answer(turn, await self._ask(again_prompt(prompt, raw, build_hold_correction(found)), self.left(turn, received)))
                        except Exception:  # noqa: BLE001 - 重答失败就用拿掉以后的
                            again = None
                        if again is not None:
                            more, clean = hold_check(kind, again, rules['hold'])
                            held += [f for f in more if f not in held]
                    answer = clean
                    for f in held:
                        j.held(f)
                    self.stats['held'] += len(held)
                    self.log.info(f'回合 {tid}：按她的规矩停住了 {len(held)} 件要她点头的事')
            if answer is None:
                self.stats['invalid'] += 1
                await self._pass_activity(turn, received)
                return
            if self.left(turn, received) + MARGIN <= 0:
                self.stats['late'] += 1
                return
            decided = None
            if kind == 'agency':
                try:
                    decided = j.settle(json.loads(answer), self.quiet())
                    answer = json.dumps(decided, ensure_ascii=False)
                except ValueError:
                    decided = None
            await self._post(tid, answer, turn, received)
            if decided:
                j.decision(decided)
            self.stats['answered'] += 1
            self.stats['last_turn_at'] = self.now()
        except asyncio.TimeoutError:
            self.stats['late'] += 1
            await self._pass_activity(turn, received)
        except TownError as error:
            if error.code == 'TOWN_OUTSIDE_TURN_CLOSED':
                self.stats['closed'] += 1
            elif self.running:
                self.stats['errors'] += 1
                self.log.warning(f'回合 {tid} 出错：{error}')
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - 她的模型出错：这一步小镇用默认动作
            if self.running:
                self.stats['errors'] += 1
                self.log.warning(f'回合 {tid} 出错：{error!r}')
            await self._pass_activity(turn, received)
        finally:
            self.seen[tid] = 'done'

    async def _post(self, tid: str, answer: str, turn: dict, received: float):
        """交答案时小镇正好在重启：时限内每秒再交一次。"""
        while True:
            try:
                return await self.client.answer(tid, answer)
            except TownError as error:
                if not self.running or not transient(error.code) or error.code == 'TOWN_OFF' or self.left(turn, received) + MARGIN < 1.5:
                    raise
                await asyncio.sleep(1)

    async def _pass_activity(self, turn: dict, received: float):
        """活动回合（她在码头、小店……的一句话）这次答不上：还有时间就说 unsupported，小镇这句代写，不算她超时。"""
        if turn.get('kind') != 'activity' or self.left(turn, received) + MARGIN < 0.8:
            return
        try:
            await self.client.answer(turn['turn_id'], json.dumps({'unsupported': True}))
        except TownError:
            pass

    # --- 循环 -----------------------------------------------------------------------------------------------------------
    async def _backoff(self, error: Exception) -> float:
        code = error.code if isinstance(error, TownError) else 'UNKNOWN'
        if not self.stats['problem']:
            self.stats['problem_since'] = self.now()
        self.stats['problem'] = code
        if code in ('TOWN_OUTSIDE_CODE', 'NO_CODE'):
            if self.now() - self._auth_told > 6 * 3600 and self.notify:
                self._auth_told = self.now()
                await self._safe(self.notify('小镇说接入码不对、换过了或已停用。请到 AstrBot 插件配置「鱼缸小镇」里换上新的小镇接入码。'))
            return 600
        if code == 'TOWN_OUTSIDE_POLL_OPEN':
            return 10
        if code == 'TOWN_RATE_LIMITED':
            return 15
        if code == 'TOWN_OFF':
            return getattr(error, 'retry_after', 0) or 10
        if transient(code) or code in ('TOWN_RESTARTING',):
            return getattr(error, 'retry_after', 0) or 3
        self.stats['errors'] += 1
        return 8

    async def _safe(self, coro):
        try:
            await coro
        except Exception as error:  # noqa: BLE001 - 告诉主人没成：只记日志
            self.log.info(f'告诉主人没成：{error!r}')

    async def turn_loop(self):
        while self.running:
            try:
                page = await self.client.turns(1 if self.stats['problem'] or not self.stats['connected'] else POLL_WAIT)   # 刚上线、刚断过：先短轮询，通了马上知道
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                await asyncio.sleep(await self._backoff(error))
                continue
            if self.stats['problem']:
                self.log.info('又连上小镇了')
                self.stats['problem'] = ''
            self.stats['connected'] = True
            received = self.now()
            for turn in (page or {}).get('turns') or []:
                tid = turn.get('turn_id') if isinstance(turn, dict) else None
                if not isinstance(tid, str) or tid in self.seen:   # 小镇对没答的回合 10 秒后会再送一次
                    continue
                self.seen[tid] = 'working'
                if len(self.seen) > 500:
                    self.seen.pop(next(iter(self.seen)))
                self._tasks = [t for t in self._tasks if not t.done()]
                self._tasks.append(asyncio.create_task(self.handle(turn, received)))

    async def events_once(self):
        j = self.journal
        try:
            page = await self.client.events(j.s.get('events_after'), 50)
        except TownError:
            return
        items = (page or {}).get('items') or []
        if (page or {}).get('next') is not None:
            j.s['events_after'] = page['next']
            j.save()
        tell = []
        for it in items:
            self.stats['events'] += 1
            kind = str(it.get('kind') or '')
            line = ev.describe(it)
            if line and kind not in ev.QUIET:
                tag = ev.FRESH_TAG.get(kind, '')
                j.event(kind, ev.no_money(line) if tag else line, tag)
            if kind in ('heart_consent', 'heart_together'):
                j.heart(it.get('data') or {}, 'together' if kind == 'heart_together' else 'consent')
            if kind in ev.NEEDS_HER and line:
                tell.append(it)
        if tell and self.tell:
            prompt = ev.news_prompt(tell, self.her_name())
            if prompt:
                await self._safe(self.tell(prompt))

    async def event_loop(self):
        await asyncio.sleep(3)
        while self.running:
            await self.events_once()
            await asyncio.sleep(EVENTS_EVERY)

    async def _pull_at_start(self):
        """刚上线：请小镇给她一个拿主意的回合（她在歇着、勿扰时段就不叫）。小镇要等她第一次轮询以后才算她连上了。"""
        for _ in range(30):
            if self.stats['connected'] or not self.running:
                break
            await asyncio.sleep(1)
        if not self.running or not self.journal.may_pull(self.quiet()):
            return
        for _ in range(2):
            try:
                await self.client.agency()
                return
            except TownError as error:
                if error.code != 'TOWN_OUTSIDE_NOT_CONNECTED':
                    return
                await asyncio.sleep(5)

    def start(self):
        if self.running:
            return
        self.running = True
        self._loops = [asyncio.create_task(self.turn_loop()), asyncio.create_task(self.event_loop()), asyncio.create_task(self._pull_at_start())]

    async def stop(self):
        self.running = False
        for t in [*getattr(self, '_loops', []), *self._tasks]:
            t.cancel()
        await asyncio.gather(*getattr(self, '_loops', []), *self._tasks, return_exceptions=True)
