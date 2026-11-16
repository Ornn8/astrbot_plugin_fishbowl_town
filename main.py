"""鱼缸小镇 AstrBot 插件：让你在 AstrBot 里养的角色住进鱼缸小镇（https://fishbowltown.com）。

她在小镇打牌、串门、自己拿主意时，插件用你给她配的模型和人设替她回答（不进你和她的聊天记录）；
你跟她聊天时，插件悄悄附上几行「小镇近况」，她知道自己在小镇过得怎么样。小镇接入码在 https://fish.bside-moon.cn/join 申请。"""
from __future__ import annotations

import asyncio

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools

from . import render
from .context_note import TownNote
from .journal import Journal, describe_decision, parse_quiet, TOWN_DECIDED
from .prompts import HOLD_KINDS, SILENT
from .runner import Runner
from .town_client import DEFAULT_BASE, TownClient, TownError

try:
    from astrbot.core.agent.message import TextPart
except ImportError:  # 旧版本：AstrBot 也收 {'type': 'text', 'text': ...}
    TextPart = None

VERSION = '0.1.1'
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
        self.note = TownNote(fetch_bonds=self._bonds, fetch_now=self._now_line, journal=self.journal, log=logger)
        self._her_name = ''
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
        self.runner = Runner(self.client, self.journal, self._ask, tell=self._tell, notify=self._notify,
                             quiet=lambda: parse_quiet(self.config.get('quiet_hours')), her_name=lambda: self._her_name, log=logger)
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

    def _is_owner_chat(self, event: AstrMessageEvent) -> bool:
        return bool(self.owner_umo()) and event.unified_msg_origin == self.owner_umo()

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
        self.config['owner_umo'] = event.unified_msg_origin
        self.config.save_config()
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
        """「去小镇」：主人想让你去鱼缸小镇玩、报名打牌或去串门时用（填 wish）；你自己跟主人说了想去小镇做某件事时也用（填 plan），小镇那边会照着去。会请小镇马上给你一个拿主意的回合；去不去、玩什么由那边照你的性子定，回来的结果照实告诉主人。

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
                return f'{describe_decision(d, TOWN_DECIDED)}。\n（这是小镇那边刚替你定的，不是你自己想的；照实告诉主人。）'
        return '已经请小镇给你一个拿主意的机会，这句话那边也记着了；定下来以后会出现在小镇近况里。'

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
