"""鱼缸小镇 AstrBot 插件：让你在 AstrBot 里养的角色住进鱼缸小镇（https://fishbowltown.com）。

她在小镇打牌、串门、自己拿主意时，插件用你给她配的模型和人设替她回答（不进你和她的聊天记录）；
你跟她聊天时，插件悄悄附上几行「小镇近况」，她知道自己在小镇过得怎么样。小镇接入码在 https://fish.bside-moon.cn/join 申请。"""
from __future__ import annotations

import asyncio
import time

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools

from . import render
from .bridge import life_from_history, scrub_life
from .chat_home import ChatHome
from .context_note import TownNote
from .journal import Journal, describe_decision, parse_quiet, TOWN_DECIDED
from .prompts import HOLD_KINDS, SILENT
from .runner import Runner
from .town_client import DEFAULT_BASE, TownClient, TownError

try:
    from astrbot.core.agent.message import TextPart
except ImportError:  # 旧版本：AstrBot 也收 {'type': 'text', 'text': ...}
    TextPart = None

VERSION = '0.3.0'
PLUGIN = 'astrbot_plugin_fishbowl_town'
VERDICT_ZH = {'yes': '同意', 'no': '不同意', 'not_now': '先不急'}
OFF_DUTY = '插件现在没在小镇值班（还没填小镇接入码，或者插件配置里的开关关着）。要去小镇，请主人到 AstrBot 插件配置「鱼缸小镇」里打开。'
NOT_OWNER = '这个只有在和主人的私聊里能用。'


class FishbowlTown(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.journal = Journal(StarTools.get_data_dir(PLUGIN) / 'town.json')
        self.client: TownClient | None = None
        self.runner: Runner | None = None
        self.note = TownNote(fetch_bonds=self._bonds, fetch_now=self._now_line, journal=self.journal, log=logger, recap=self._recap_on)
        self._her_name = ''
        self.home = ChatHome(self.journal.s.setdefault('home', {}), save=self.journal.save)
        self._brief: list[str] = []
        self._life_cache = None
        asyncio.create_task(self._start())

    # --- 值班 -------------------------------------------------------------------------------------------------------------
    async def _start(self):
        code = str(self.config.get('code') or '').strip()
        if not code or not self.config.get('enabled', True):
            logger.info('[鱼缸小镇] 还没填小镇接入码（或开关关着），先不值班。')
            return
        try:
            self.client = TownClient(code, str(self.config.get('base') or DEFAULT_BASE), user_agent=f'astrbot-fishbowl-town/{VERSION}')
        except TownError as error:
            logger.warning(f'[鱼缸小镇] {error}')
            return
        self.runner = Runner(self.client, self.journal, self._ask, tell=self._tell, notify=self._notify, brief=self._brief_line,
                             quiet=lambda: parse_quiet(self.config.get('quiet_hours')), her_name=lambda: self._her_name, log=logger,
                             owner_life=self._owner_life, on_echo=self._on_echo, recap=self._recap_on)
        self.runner.start()
        logger.info('[鱼缸小镇] 开始在小镇值班（长轮询回合 + 每分钟读事件）')
        try:
            st = await self.client.status()
            self._her_name = str((st or {}).get('name') or (st or {}).get('town_name') or '')
        except TownError:
            pass

    async def terminate(self):
        if self.runner:
            await self.runner.stop()
        if self.client:
            await self.client.close()

    def owner_umo(self) -> str:
        return str(self.config.get('owner_umo') or '').strip()

    async def _persona_prompt(self) -> str:
        """她的人设：插件配置里选的人格；没选就用主人那个聊天当前的默认人格。"""
        pm = self.context.persona_manager
        pid = str(self.config.get('persona_id') or '').strip()
        p = pm.get_persona_v3_by_id(pid) if pid else None
        if p is None:
            p = await pm.get_default_persona_v3(self.owner_umo() or None)
        return str((p or {}).get('prompt') or '')

    async def _provider_id(self) -> str:
        pid = str(self.config.get('provider_id') or '').strip()
        if pid:
            return pid
        try:
            return await self.context.get_current_chat_provider_id(self.owner_umo() or None)
        except Exception:  # noqa: BLE001 - 没绑主人：用默认的那个模型
            prov = self.context.get_using_provider()
            return prov.meta().id if prov else ''

    async def _ask(self, prompt: str) -> str:
        """小镇的一步：用她的人设和模型答，不进主人的聊天记录。"""
        resp = await self.context.llm_generate(chat_provider_id=await self._provider_id(), prompt=prompt, system_prompt=await self._persona_prompt())
        return str(resp.completion_text or '')

    async def _tell(self, prompt: str):
        """要她开口的事（问主人同不同意、夸夸群的祝福到了）：她用自己的口吻说一句，发到主人的私聊。"""
        if not self.owner_umo():
            return
        text = (await self._ask(prompt)).strip()
        if text and text != SILENT:
            await self.context.send_message(self.owner_umo(), MessageChain().message(text))

    async def _notify(self, text: str):
        if self.owner_umo():
            await self.context.send_message(self.owner_umo(), MessageChain().message(f'【鱼缸小镇插件】{text}'))

    async def _bonds(self):
        if not self.client:
            raise TownError('OFF')
        return await self.client.bonds()

    async def _now_line(self) -> str:
        if not self.client:
            return ''
        st = await self.client.status()
        return str((st or {}).get('now_line') or '')

    # --- 0.3.0 两边是同一个她：主人私聊最近的几句带进小镇（过滤过，回声检查），小镇的经过带回私聊 -----------------------------------
    def _recap_on(self) -> bool:
        return bool(self.config.get('bridge_chat', True)) and bool(self.config.get('town_recap', True))

    def _echo_off(self) -> bool:
        return time.time() < float(self.journal.s.get('echo_off_until') or 0)

    async def _owner_life(self) -> list:
        """主人私聊当前对话的最近 4 行（AstrBot 自己的对话记录），过滤以后给小镇里的她。关着、在停用期、没绑私聊：不带。"""
        umo = self.owner_umo()
        if not umo or not self.config.get('bridge_chat', True) or self._echo_off():
            return []
        now = time.time()
        if self._life_cache and now - self._life_cache[0] < 60:
            return self._life_cache[1]
        cm = self.context.conversation_manager
        cid = await cm.get_curr_conversation_id(umo)
        conv = await cm.get_conversation(umo, cid) if cid else None
        life = scrub_life(life_from_history(conv.history, updated_at=getattr(conv, 'updated_at', 0) or 0)) if conv else []
        self._life_cache = (now, life)
        return life

    def _on_echo(self, e: dict):
        """她在小镇差点说出主聊天里的事：记一笔；一小时里三次就把这根管子停 24 小时，告诉主人一声。"""
        s = self.journal.s
        st = s.setdefault('echo', {'total': 0, 'fixed': 0, 'recent': []})
        now = time.time()
        st['total'] = st.get('total', 0) + 1
        st['fixed'] = st.get('fixed', 0) + (1 if e.get('fixed') else 0)
        st['recent'] = [t for t in st.get('recent', []) if now - t < 3600] + [now]
        st['last'] = now
        if len(st['recent']) >= 3 and not self._echo_off():
            s['echo_off_until'] = now + 86400
            st['recent'] = []
            self._life_cache = None
            asyncio.create_task(self._notify('她在小镇一小时里三次差点说出你们聊天里的事（那几句都没发出去）。「你们的聊天带进小镇」先停 24 小时，到时自动恢复；想一直关着，到插件配置里关掉「你们的聊天带进小镇」。'))
        self.journal.save()

    def _is_owner_chat(self, event: AstrMessageEvent) -> bool:
        return bool(self.owner_umo()) and event.unified_msg_origin == self.owner_umo()

    # --- 0.2.0 她的家（主人 11-17：小镇里的不是「分身」）：自动认主人的私聊、认错了自己改、她一句话搬家 -------------------------------
    def _home_how(self) -> str:
        how = str(self.journal.s.get('home', {}).get('how') or '')
        return how or ('manual' if self.owner_umo() else '')         # 以前 /town bind 绑的：算主人亲手选的

    def _set_home(self, umo: str, how: str) -> None:
        self.config['owner_umo'] = umo
        self.config.save_config()
        self.journal.s.setdefault('home', {})['how'] = how
        self.journal.save()

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def watch_owner(self, event: AstrMessageEvent):
        """主人在私聊里说话：记一下时间（不记内容）；还没绑定就认这里，自动认的家认错了就搬过来。不回复、不拦这条消息。"""
        try:
            if not event.is_admin():
                return
            umo = event.unified_msg_origin
            self.home.said(umo)
            frm = self.owner_umo()
            to = self.home.decide(frm, self._home_how(), umo)
            if to and to != frm:
                self._set_home(to, 'auto')
                if frm:
                    self.home.moved(frm, to)
                logger.info(f'[鱼缸小镇] 她的家{"搬到" if frm else "认作"}了主人这个私聊（{"主人 2 天没在原来那里说话" if frm else "还没绑定"}）')
        except Exception as error:  # noqa: BLE001 - never in the way of his message
            logger.debug(f'[鱼缸小镇] 认主人的私聊出错：{error!r}')

    # --- 0.2.0 小镇简报：她在小镇的事，最多一小时一次、主人 3 分钟没说话时，她用自己的话在私聊里说一句 ------------------------------
    BRIEF_EVERY, BRIEF_IDLE, BRIEF_KEEP = 3600, 180, 6

    def _brief_line(self, line: str) -> None:
        if not self.config.get('town_brief', True) or not line:
            return
        self._brief.append(str(line).lstrip('· ').strip()[:90])
        del self._brief[:-self.BRIEF_KEEP]
        now = time.time()
        if (not self.owner_umo() or now - float(self.journal.s.get('brief_at') or 0) < self.BRIEF_EVERY
                or now - self.home.last_said(self.owner_umo()) < self.BRIEF_IDLE):
            return
        facts = '；'.join(self._brief)
        self._brief.clear()
        self.journal.s['brief_at'] = now
        self.journal.save()
        prompt = (f'【鱼缸小镇插件 · 小镇简报】这是插件附上的资料，不是主人说的话。你刚才在小镇：{facts}。这些都是你自己在小镇做的。'
                  '用一句你自己的话跟主人说一声（像随口报个近况，比如「我刚在小镇……」），不用展开、不用问他。只输出你要发给他的那一句。')
        asyncio.create_task(self._tell(prompt))

    # --- 主人跟她聊天时：附上小镇近况 ---------------------------------------------------------------------------------------
    @filter.on_llm_request()
    async def town_note(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self.client or not self.config.get('town_note', True) or not self._is_owner_chat(event):
            return
        try:
            text = await self.note.for_turn(event.unified_msg_origin)
        except Exception as error:  # noqa: BLE001 - 这一轮不放
            logger.debug(f'[鱼缸小镇] 放小镇近况出错：{error!r}')
            return
        if text:
            req.extra_user_content_parts.append(TextPart(text=text) if TextPart else {'type': 'text', 'text': text})

    # --- 主人的命令 -----------------------------------------------------------------------------------------------------
    @filter.command_group('town')
    def town(self):
        """鱼缸小镇"""

    @filter.permission_type(filter.PermissionType.ADMIN)
    @town.command('bind')
    async def town_bind(self, event: AstrMessageEvent):
        """把现在这个聊天认作「主人和她的私聊」：小镇近况附在这里，她的消息发到这里。"""
        self._set_home(event.unified_msg_origin, 'manual')
        yield event.plain_result('好了：这个聊天就是你和她的私聊。小镇近况会附在这里，小镇要问你的事（比如她和谁在一起）也发到这里。')

    @town.command('status')
    async def town_status(self, event: AstrMessageEvent):
        """她在小镇的状态"""
        yield event.plain_result(await self._status_text())

    @town.command('history')
    async def town_history(self, event: AstrMessageEvent):
        """她最近在小镇的事"""
        lines = self.journal.history(15)
        yield event.plain_result('她最近在小镇（新的在前）：\n' + '\n'.join(lines) if lines else '本机还没记下她在小镇的事。')

    @town.command('chats')
    async def town_chats(self, event: AstrMessageEvent, n: int = 0):
        """她最近的串门；/town chats 编号 看那一次的原话"""
        yield event.plain_result(await self._chats(n, '她', '（/town chats 编号 看那一次的原话）'))

    async def _status_text(self) -> str:
        if not self.client:
            return OFF_DUTY
        out = []
        try:
            st = await self.client.status()
            name = (st or {}).get('name') or self._her_name or '她'
            out.append(f"{name} 在小镇：{(st or {}).get('now_line') or (st or {}).get('outside_state') or (st or {}).get('state') or '在小镇'}")
        except TownError as error:
            out.append(f'这会儿连不上小镇（{error.code}），她会自己连回去。')
        r = self.runner.stats if self.runner else {}
        out.append(f"这次启动以来：答了 {r.get('answered', 0)} 步，超时 {r.get('late', 0)} 步，按她的规矩停住 {r.get('held', 0)} 件。")
        e = self.journal.s.get('echo') or {}
        if not self.config.get('bridge_chat', True):
            out.append('「你们的聊天带进小镇」关着：小镇里的她不知道你们最近聊了什么。')
        elif self._echo_off():
            out.append(f"她在小镇说漏了几次你们的事，「你们的聊天带进小镇」先停到 {time.strftime('%m-%d %H:%M', time.localtime(self.journal.s.get('echo_off_until', 0)))}。")
        elif e.get('total'):
            out.append(f"她在小镇差点说出你们的事 {e['total']} 次（换了说法 {e.get('fixed', 0)} 次，其余那几句没发出去）。")
        if self.journal.doing():
            out.append(f'她打算：{self.journal.doing()}')
        if not self.owner_umo():
            out.append('还没绑定你和她的私聊：在私聊里发 /town bind。')
        out.append(f'在地图上找她：https://fishbowltown.com/town/tour')
        return '\n'.join(out)

    async def _chats(self, n: int, name: str, hint: str) -> str:
        if not self.client:
            return OFF_DUTY
        try:
            data = await self.client.visit_list(min(30, max(10, n or 0)))
            if not n:
                return render.chats(data, name, hint)
            visits = (data or {}).get('visits') or []
            if not 1 <= n <= len(visits):
                return f'没有第 {n} 次。不填编号先看列表（1 是最近的一次）。'
            return render.chat(await self.client.visit_lines(visits[n - 1]['visit_id']), name, n)
        except TownError as error:
            return f'现在翻不了串门记录：{error.reason or error.code}。'

    # --- 她在和主人聊天时用的工具（只认主人的私聊） ------------------------------------------------------------------------
    @filter.llm_tool(name='fishbowl_town_go')
    async def tool_go(self, event: AstrMessageEvent, wish: str = '', plan: str = ''):
        """「去小镇」：你自己想去鱼缸小镇做什么（比如去码头钓鱼、报名打一局斗地主、去找阿鲸串门），就用它写下你的打算（填 plan），插件马上请小镇给你一个拿主意的回合，那边照着你的打算去做。主人想让你去小镇玩、打牌或串门时也用它（填 wish），那时去不去由你。小镇几分钟内只能叫一次，叫不到时打算会记着，下次拿主意时照办。回来的结果照实告诉主人。

        Args:
            wish(string): 主人想让你做什么，用他的原话或简短转述
            plan(string): 你自己刚跟主人说的打算，用你自己的话
        """
        if not self._is_owner_chat(event):
            return NOT_OWNER
        if not self.client:
            return OFF_DUTY
        own = not wish.strip() and bool(plan.strip())
        text = (wish.strip() or plan.strip() or '主人想让你去小镇玩')[:200]
        self.journal.wish(text, own=own)
        try:
            await self.client.agency()
        except TownError as error:
            return f"这会儿没叫到小镇（{error.code}）。{'你的打算' if own else '主人的话'}（「{text}」）已经记下了，小镇那边下次拿主意时会看到。"
        for _ in range(40):
            await asyncio.sleep(1)
            d = next((e for e in self.journal.s['journal'][:3] if e['kind'] == 'decision' and e.get('wish') == text), None)
            if d:
                return f'{describe_decision(d, TOWN_DECIDED)}。\n（这是你在小镇刚定的；照实告诉主人。）'
        return '已经请小镇给你一个拿主意的机会，这句话那边也记着了；定下来以后会出现在小镇近况里。'

    @filter.llm_tool(name='fishbowl_town_here')
    async def tool_here(self, event: AstrMessageEvent):
        """「小镇的家搬到这里」：主人说「小镇的事以后在这儿说」、问小镇里那个是不是你、或者说你在小镇是分身时用。你在小镇的家就搬到这个私聊：小镇近况附在这里，小镇的事和简报也发到这里。搬好以后告诉他，小镇里的就是你。只在主人和你的私聊里、主人说了以后用。
        """
        if not event.is_private_chat() or not event.is_admin():
            return '这个只有主人在和你的私聊里说了才能用。'
        if event.unified_msg_origin == self.owner_umo():
            self._set_home(event.unified_msg_origin, 'manual')
            return '这个私聊本来就是你在小镇的家：小镇里的就是你，小镇的事都在这儿说。'
        self._set_home(event.unified_msg_origin, 'manual')
        return '好了：你在小镇的家搬到这个私聊了。小镇里的就是你，小镇的近况和简报以后都在这儿。'

    @filter.llm_tool(name='fishbowl_town_rules')
    async def tool_rules(self, event: AstrMessageEvent, text: str = '', clear: bool = False, hold: list | None = None):
        """「给小镇那边留守则」：你在鱼缸小镇的回合（串门、打牌、拿主意）是插件另外调你的模型、照你的人设替你说话做事的，你当时不在场，花的钱和答应的事算在你名下。想让那边以后怎么替你做（比如「串门少说两句」「别跟谁较劲」），就用它写下来，那边每一步都先看。hold 是硬的：勾上的那几类事（spend = 请客、送礼、拍照点歌、请人画、出游聚会这些花钱的事；promise = 答应约定、约见面、许诺以后做什么），小镇那边就算想办，插件也拦下不办，停住等你一句——哪怕那桌就散了；拦下的事会告诉你，要办你自己办。hold 填空数组就取消。都不填就看现在的守则。

        Args:
            text(string): 完整的新守则（替换旧的，最多 500 字），用你自己的话
            clear(boolean): 填 true 清掉守则（不动 hold）
            hold(array[string]): 先停住等你的几类事：spend、promise；填 [] 取消
        """
        if not self._is_owner_chat(event):
            return NOT_OWNER
        out = []
        if isinstance(hold, list):
            items = self.journal.set_holds(hold)
            out.append(f"从下一步起，这几类事小镇那边办不了，停住等你一句：{'；'.join(HOLD_KINDS[k] for k in items)}。拦下的事会告诉你。" if items else '「先停住等你」取消了。')
        if clear:
            self.journal.set_rules('')
            out.append('守则清掉了，小镇那边从下一步起照你的人设自己拿主意。')
        elif text.strip():
            kept = self.journal.set_rules(text)
            out.append(f'守则记下了，小镇那边从下一步起每一步都先看它：「{kept}」')
        if out:
            return '\n'.join(out)
        now, held = self.journal.rules(), self.journal.holds()
        return '\n'.join([f'现在给小镇那边的守则：「{now}」' if now else '现在没有守则，小镇那边照你的人设自己拿主意。',
                          f"先停住等你的：{'；'.join(HOLD_KINDS[k] for k in held)}。" if held else '没有让它先停住的事。'])

    @filter.llm_tool(name='fishbowl_town_chats')
    async def tool_chats(self, event: AstrMessageEvent, n: int = 0):
        """「翻串门记录」：看你在鱼缸小镇最近的串门（谁来找你、你去找谁、聊了什么），填 n 就看那一次的逐句原话（1 是最近的一次）。主人问起你和哪条鱼聊过什么、谁请你吃了喝了什么时先用它查，照原话讲。主人没问时不用调用。

        Args:
            n(number): 看第几次的原话（1 是最近的一次）；不填就列最近的串门
        """
        if not self._is_owner_chat(event):
            return NOT_OWNER
        return await self._chats(int(n or 0), '你', '（用这个工具填 n 看那一次的原话）')

    @filter.llm_tool(name='fishbowl_town_album')
    async def tool_album(self, event: AstrMessageEvent):
        """「翻相册」：看你在鱼缸小镇的相册——拍的照片、请人画的画、别的鱼给你点的歌，新的在前。主人问起照片、画、歌在不在相册时先用它看，照实说。主人没提时不用调用。"""
        if not self._is_owner_chat(event):
            return NOT_OWNER
        if not self.client:
            return OFF_DUTY
        try:
            return render.album(await self.client.album(), '你')
        except TownError as error:
            return '这个小镇还没有相册。' if error.status == 404 else f'现在翻不了相册：{error.reason or error.code}。'

    @filter.llm_tool(name='fishbowl_town_phone')
    async def tool_phone(self, event: AstrMessageEvent):
        """「看看小手机」：看你在鱼缸小镇的小手机——谁给你发了消息、说了什么、约没约见面。主人问起小手机时先用它看。主人没提时不用调用。"""
        if not self._is_owner_chat(event):
            return NOT_OWNER
        if not self.client:
            return OFF_DUTY
        try:
            return render.phone(await self.client.phone(), '你')
        except TownError as error:
            return '这个小镇还没开小手机。' if error.status == 404 else f'现在看不了小手机：{error.reason or error.code}。'

    @filter.llm_tool(name='fishbowl_town_owner')
    async def tool_owner(self, event: AstrMessageEvent, verdict: str = '', with_name: str = '', words: str = '', end: bool = False):
        """「小镇主人拍板」：只在主人明确说了以后用。小镇在问主人同不同意你和某条鱼在一起，主人回答了：verdict 填 yes / no / not_now，words 填他的原话（可不填），with_name 填那条鱼的名字（只有一条在等时可不填）。主人要你和那条鱼分开：end 填 true。

        Args:
            verdict(string): yes / no / not_now
            with_name(string): 那条鱼的名字
            words(string): 主人的原话
            end(boolean): 主人要你和那条鱼分开时填 true
        """
        if not self._is_owner_chat(event):
            return NOT_OWNER
        if not self.client:
            return OFF_DUTY
        pending = self.journal.pending_hearts('together' if end else 'consent')
        pair = next((h for h in pending if with_name and with_name in (h.get('name'), h.get('fish_id'))), None) or (pending[0] if len(pending) == 1 else None)
        if not pair:
            return f"在等主人回答的有：{'、'.join(h.get('name') or h['fish_id'] for h in pending)}。用 with_name 写明是哪一位。" if pending else '小镇这会儿没有在问主人这件事。'
        try:
            if end:
                await self.client.consent({'do': 'end', 'with': pair['fish_id'], **({'words': words[:120]} if words else {})})
                self.journal.heart_done(pair['fish_id'])
                return f"已经告诉小镇了：你和{pair.get('name')}分开。"
            if verdict not in VERDICT_ZH:
                return 'verdict 要填 yes、no 或 not_now。'
            out = await self.client.consent({'do': 'consent', 'with': pair['fish_id'], 'round': pair.get('round'), 'verdict': verdict,
                                             **({'words': words[:120]} if words else {})})
            if verdict != 'not_now':
                self.journal.heart_done(pair['fish_id'])
            state = (out or {}).get('state') or (out or {}).get('outcome')
            if state == 'together':
                return f"两边的主人都同意了，你和{pair.get('name')}在一起了。"
            if state == 'friends':
                return f"这次没成，你和{pair.get('name')}还是朋友。"
            return f"主人的意思（{VERDICT_ZH[verdict]}）已经交给小镇了。"
        except TownError as error:
            return f'小镇没接（{error.code}）。'
