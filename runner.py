"""核心循环（DSH 插件 runner.js 的 Python 版）：长轮询小镇的回合 -> 交给她的模型回答 -> 时限内交回；每分钟读一次事件。
不认识 AstrBot：她的模型（ask）、告诉主人（tell / notify）都是传进来的，测试里换成假的。"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from . import events as ev
from .chat_decide import NOTE_EXECUTED
from .bridge import ECHO_CORRECTION, echo_check, echo_index, recap_game, recap_visit
from .isle import Isle, PicsRefused, isle_version_of, isle_within_s, unattach
from .journal import play_of
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
    def __init__(self, client, journal, ask, *, tell=None, notify=None, brief=None, quiet=lambda: None, her_name=lambda: '', log=None, now=time.time,
                 owner_life=None, on_echo=None, recap=lambda: True, decide=None, see=None):
        self.client, self.journal, self.ask = client, journal, ask
        self.see = see                # 0.4.2 看图：查到的图能不能附给她（main._see_pics）
        self.decide = decide          # 0.4.0「主聊天拿主意」（chat_decide.ChatDecide）
        self.tell, self.notify, self.brief = tell, notify, brief
        self.quiet, self.her_name = quiet, her_name
        self.owner_life, self.on_echo, self.recap_on = owner_life, on_echo, recap   # 0.3.0 两边是同一个她
        self.life_refs: dict = {}   # 已经带过「你和主人最近的事」的那几局（一局只在第一轮带）
        self.log = log or logging.getLogger('fishbowl_town')
        self.now = now
        self.isle = Isle(client, journal, self.log)   # 0.4.2 鱼缸岛：规矩按版本存一份 + 先查再出手
        self.seen: dict = {}
        self.stats = {'turns': 0, 'answered': 0, 'invalid': 0, 'late': 0, 'closed': 0, 'errors': 0, 'events': 0, 'held': 0,
                      'connected': False, 'problem': '', 'last_turn_at': 0, 'echo': 0}
        self.running = False
        self._tasks: list = []
        self._auth_told = 0

    # --- 一个回合 -------------------------------------------------------------------------------------------------------
    def left(self, turn: dict, received: float) -> float:
        """还剩几秒能答。deadline 是小镇的绝对时间；电脑时钟可能不准，再用「收到以后过了多久」兜底。
        0.4.2：鱼缸岛上拿主意的回合小镇等 packet.isle.within_s（120 秒），留给一回合里先查再出手；本地上限比它早 2 秒。"""
        within = isle_within_s(turn)
        by_receipt = (within or POLL_WAIT) - (self.now() - received)
        try:
            by_deadline = float(turn.get('deadline') or 0) - self.now() if turn.get('deadline') else float('inf')
        except (TypeError, ValueError):
            by_deadline = float('inf')
        left = by_receipt if not -60 < by_deadline < (within or 60) + 60 else min(by_deadline, by_receipt)
        local = within - 2 if within else ANSWER_WITHIN
        return min(left, local - (self.now() - received) + MARGIN) - MARGIN

    async def _ask(self, prompt: str, timeout: float, images=None) -> str:
        return await asyncio.wait_for(self.ask(prompt, images=images) if images else self.ask(prompt), timeout=max(1.0, timeout))

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
            # 0.4.0：主意由聊天里的她定（chat_decide.py）；这里只照着办 / 急事先答 / 两次没回先替她定，其余插件直接回小镇「过一会儿再叫我」
            route = self.decide.route(turn) if kind == 'agency' and self.decide else None
            if route and route.get('answer'):
                await self._post(tid, route['answer'], turn, received)
                self.stats['chat_decide'] = self.stats.get('chat_decide', 0) + 1
                self.log.info(f'拿主意的回合 {tid}：等聊天里的她定，小镇那边先等一会儿')
                return
            role = (route or {}).get('role', '')
            rules = {'text': j.rules(), 'hold': j.holds()}
            mind = {'rules': rules}
            if kind == 'agency':
                mind.update(recent=j.recent_for_agency(), wish=j.pending_wish(), wish_own=j.wish_is_own(), role=role)
            if kind == 'visit':
                plan = (j.pending_wish() if j.wish_is_own() else '') or j.doing()
                if plan:
                    mind['plan'] = plan
            # 0.3.0：她和主人最近的几句（过滤过）。拿主意、串门每次带；牌局只在一局的第一轮带。回声检查每一轮都比
            life = await self._life()
            secret = None
            if life:
                ref = str((turn.get('packet') or {}).get('ref') or '')
                if kind in ('agency', 'visit'):
                    mind['life'] = life
                elif kind == 'game' and ref and ref not in self.life_refs:
                    mind['life'] = life
                    self.life_refs[ref] = self.now()
                    if len(self.life_refs) > 200:
                        self.life_refs.pop(next(iter(self.life_refs)))
                msgs = (turn.get('packet') or {}).get('messages') or []
                secret = echo_index(life, allow=' '.join(str(m.get('content') if isinstance(m, dict) else '') for m in msgs if isinstance(msgs, list)))
            prompt = build_turn_prompt(turn, seconds_left=int(self.left(turn, received)), **mind)
            # 0.4.2 鱼缸岛：规矩全文按版本存一份，放在最前面（每一问都是新的一问：人设 + 规矩每次一样，前缀缓存才命中）
            isle_key = isle_version_of(turn) if kind == 'agency' else ''
            isle_text = await self.isle.prefix(turn) if isle_key else ''
            if isle_text:
                prompt = f'{isle_text}\n\n{prompt}'
            raw = await self._ask(prompt, self.left(turn, received))
            answer = to_answer(turn, raw)
            if answer is None and self.left(turn, received) >= RETRY_MIN:
                raw = await self._ask(again_prompt(prompt, raw, build_correction(turn)), self.left(turn, received))
                answer = to_answer(turn, raw)
            # 0.4.2 鱼缸岛：她要先查（{"world":{"do":"look",…}}）：插件向小镇要那一页递回给她，最多 3 次，然后她出手。
            # 每一问接着上一问往后写（她刚才的回答 + 查到的那页），前面不变，缓存照样命中
            if answer is not None and isle_key:
                conv = {'text': prompt, 'said': raw}

                async def ask_more(follow: str, images=None) -> str:
                    conv['text'] = again_prompt(conv['text'], conv['said'], follow)
                    try:   # 0.4.2 查到的图只附在这一问里（下一问是新的一问，她看图后说的话留在字里）
                        try:
                            conv['said'] = await self._ask(conv['text'], self.left(turn, received), images=images)
                        except PicsRefused:   # 她的模型不收图：同一问只给字再问一次
                            conv['text'] = unattach(conv['text'])
                            conv['said'] = await self._ask(conv['text'], self.left(turn, received))
                    except Exception:  # noqa: BLE001 - 没答上：read_loop 用她上一次的回答
                        conv['said'] = ''
                    return conv['said']
                r = await self.isle.read_loop(turn, answer, ask=ask_more, to_answer=lambda x: to_answer(turn, x),
                                              left=lambda: self.left(turn, received), min_s=RETRY_MIN, see=self.see)
                if r['reads']:
                    self.stats['isle_reads'] = self.stats.get('isle_reads', 0) + r['reads']
                    self.log.info(f"回合 {tid}：她先查了 {r['reads']} 次岛上的情况{'（没查完就到点了）' if r.get('cut') else ''}")
                answer = r['answer']
                prompt, raw = conv['text'], conv['said'] or raw
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
            # 0.3.0 回声检查：她要说出去的话里带出了主聊天里的事（连着 6 个汉字一样 / 同一串数字）：先请她重答，还带着就把那几句去掉
            if answer is not None and secret and not secret['empty']:
                fmt = 'text' if ((turn.get('packet') or {}).get('reply') or {}).get('format') == 'text' else 'json'
                hits, clean = echo_check(answer, secret, fmt=fmt)
                if hits:
                    self.stats['echo'] += 1
                    fixed = False
                    if self.left(turn, received) >= RETRY_MIN:
                        try:
                            again = to_answer(turn, await self._ask(again_prompt(prompt, answer, ECHO_CORRECTION), self.left(turn, received)))
                        except Exception:  # noqa: BLE001 - 重答失败就用去掉以后的
                            again = None
                        if again is not None:
                            h2, c2 = echo_check(again, secret, fmt=fmt)
                            if not h2:
                                answer, fixed = again, True
                            else:
                                clean = c2
                    if not fixed:
                        answer = clean
                    if self.on_echo:
                        try:
                            self.on_echo({'kind': kind, 'fixed': fixed, 'n': len(hits)})
                        except Exception:  # noqa: BLE001 - 只是记一笔
                            pass
                    self.log.info(f"回合 {tid}：她的话带出了主聊天里的事，{'换了个说法' if fixed else '那几句没发'}")
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
                    # 0.4.0：照她在聊天里定的办的，note 标一下（小镇那边数得出；主人在「战绩」里看得到）
                    if role == 'execute' and isinstance(decided, dict) and not str(decided.get('note') or '').startswith('办不了'):
                        decided['note'] = (NOTE_EXECUTED + ('：' + str(decided['note']) if decided.get('note') else ''))[:200]
                    answer = json.dumps(decided, ensure_ascii=False)
                except ValueError:
                    decided = None
            await self._post(tid, answer, turn, received)
            if decided:
                j.decision(decided)
                if role and self.decide:
                    try:
                        self.decide.after(role, decided)
                    except Exception:  # noqa: BLE001 - 只是记一笔
                        pass
            if kind == 'game':   # 牌桌上这一手记进本子，那局打完时连同结果给聊天里的她
                try:
                    a = json.loads(answer)
                    j.move((turn.get('packet') or {}).get('ref'), play_of(turn, a), a.get('say') if isinstance(a.get('say'), str) else '')
                except (ValueError, AttributeError):
                    pass
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

    async def _life(self) -> list:
        if not self.owner_life:
            return []
        try:
            return list(await asyncio.wait_for(self.owner_life(), 2) or [])
        except Exception:  # noqa: BLE001 - 读不到就不带
            return []

    async def _recap_visit(self, ref: str):
        try:
            one = await self.client.visit_lines(ref)
            self.journal.recap(ref, 'visit', recap_visit(one, her_names=[self.her_name()]))
        except Exception as error:  # noqa: BLE001 - 取不到原话就只有一行结果
            self.log.debug(f'取串门原话整理经过失败：{error!r}')

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
            d = it.get('data') if isinstance(it.get('data'), dict) else None
            # 超时代打的一手记到那局上；一局打完，结果后面带上那边替她出了几手、超时几手，本子里另记一行每一手
            if kind == 'outside_turn' and d and d.get('what') == 'timeout' and d.get('ref'):
                j.miss(d['ref'])
            table = j.table(d.get('game_id') or d.get('ref')) if kind == 'digest' and d else None
            on = self.recap_on()
            if kind == 'digest' and d and on:
                ref = str(d.get('game_id') or d.get('ref') or '')
                raw = j.table_raw(ref) or {}
                result = re.sub(r'^[^：]{1,8}：', '', re.sub(r'[。.]\s*$', '', str(d.get('facts') or d.get('summary') or '')))
                j.recap(ref, 'game', recap_game({'title': d.get('game_name') or d.get('game_zh') or '', 'result': result, 'moves': raw.get('moves') or []}))
            if kind == 'visit' and d and on and (d.get('visit_id') or d.get('ref')):
                self._tasks.append(asyncio.create_task(self._recap_visit(str(d.get('visit_id') or d.get('ref')))))
            if table:
                it = {**it, 'data': {**d, 'facts': re.sub(r'[。.]\s*$', '', str(d.get('facts') or d.get('summary') or '')) + table['note']}}
            line = ev.describe(it)
            if kind in ('outside_receipt', 'service') and d is not None:
                own = ev.own_line(kind, d)               # 0.3.3：她自己做的事，一行她看得懂的话
                if own:
                    j.event(kind, own, ev.own_tag(kind, d))
                    if kind == 'service' and self.brief:
                        try:
                            self.brief(own)                  # 小镇简报照旧有这件（回执本来就不进简报）
                        except Exception:  # noqa: BLE001
                            pass
                    line = None
            if line and kind not in ev.QUIET:
                tag = ev.FRESH_TAG.get(kind, '')
                j.event(kind, ev.no_money(line) if tag else line, tag)
                if self.brief and kind not in ('outside_turn', 'outside_receipt'):
                    try:
                        self.brief(ev.no_money(line))          # 0.2.0 小镇简报：她在小镇的事攒着，一阵一阵跟主人说
                    except Exception:  # noqa: BLE001 - the brief never stops the events
                        pass
            if table and table['moves']:
                j.event('table', table['moves'], '', 300)
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
