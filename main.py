"""鱼缸小镇 AstrBot 插件：让你在 AstrBot 里养的角色住进鱼缸小镇（https://fishbowltown.com）。

她在小镇打牌、串门、自己拿主意时，插件用你给她配的模型和人设替她回答（不进你和她的聊天记录）；
你跟她聊天时，插件悄悄附上几行「小镇近况」，她知道自己在小镇过得怎么样。小镇接入码在 https://fish.bside-moon.cn/join 申请。"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import time

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools

from . import isle_tools, render, updater
from .bridge import life_from_history, scrub_life
from .chat_decide import PLAN_MARK, ChatDecide, strip_plan
from .chat_home import ChatHome
from .context_note import TownNote
from .guide import guide_text
from .journal import Journal, describe_decision, parse_quiet, TOWN_DECIDED
from .prompts import HOLD_KINDS, SILENT
from .isle import PicsRefused
from .runner import Runner
from .old_town import closed_text, isle_status_line, now_with_isle
from .town_client import DEFAULT_BASE, TownClient, TownError, closed
from .cobuild import WATCH_S, Cobuild, at_of, blender_text, numbers_line

# 0.4.2 岛上查到的图：模型明说不收图的错（OpenAI / Gemini / Claude / DeepSeek 各家的说法），记一天
PICS_REFUSED = re.compile(r'image|vision|multimodal|multi-modal|image_url|不支持.{0,6}图|图片', re.I)
NO_PICS_S = 86400

try:
    from astrbot.core.agent.message import TextPart
except ImportError:  # 旧版本：AstrBot 也收 {'type': 'text', 'text': ...}
    TextPart = None

VERSION = '0.4.13'
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
        # 0.4.0「主聊天拿主意」：去哪、做什么由私聊里的她定（chat_decide.py）
        self.decide = ChatDecide(self.journal, ask_main=self._ask_main, home_umo=self.owner_umo, idle_s=self._owner_idle_s, config=self.config, log=logger)
        self.note = TownNote(fetch_bonds=self._bonds, fetch_now=self._now_line, journal=self.journal, log=logger, recap=self._recap_on, decide=self.decide)
        self._her_name = ''
        self.home = ChatHome(self.journal.s.setdefault('home', {}), save=self.journal.save)
        self._brief: list[str] = []
        self._life_cache = None
        self._install_task = None                  # never cancelled by terminate(): AstrBot calls terminate() in the middle of it
        self._no_pics: dict = {}                   # 0.4.2 不收图的模型 → 到什么时候前不再递图
        # 0.4.7 主人和鱼一起盖楼（cobuild.py）：文件在插件数据目录的 build/ 里；主人亲手打的字由 watch_owner 交进来（交楼、确认只认这个）
        self.cobuild = Cobuild(StarTools.get_data_dir(PLUGIN) / 'build', log=logger, journal=self.journal,
                               blender_path=lambda: str(self.config.get('blender_path') or ''), town=lambda: self.client, tell=self._build_tell,
                               is_owner_chat=lambda key: bool(self.owner_umo()) and key == self.owner_umo())
        self._build_said = ('', 0.0)               # 背景备注里上一次放的那行「正在盖的楼」（没变就不重复放）
        asyncio.create_task(self._start())
        self._update_loop_task = asyncio.create_task(self._update_loop())
        self._build_loop_task = asyncio.create_task(self._build_loop())

    # --- 值班 -------------------------------------------------------------------------------------------------------------
    async def _start(self):
        code = str(self.config.get('code') or '').strip()
        if not code or not self.config.get('enabled', True):
            logger.info('[鱼缸小镇] 还没填小镇接入码（或开关关着），先不值班。')
            return
        try:
            self.client = TownClient(code, str(self.config.get('base') or DEFAULT_BASE), user_agent=f'astrbot-fishbowl-town/{VERSION}', log=logger)
        except TownError as error:
            logger.warning(f'[鱼缸小镇] {error}')
            return
        self.runner = Runner(self.client, self.journal, self._ask, tell=self._tell, notify=self._notify, brief=self._brief_line,
                             quiet=lambda: parse_quiet(self.config.get('quiet_hours')), her_name=lambda: self._her_name, log=logger,
                             owner_life=self._owner_life, on_echo=self._on_echo, recap=self._recap_on, decide=self.decide, see=self._see_pics,
                             on_event=self.cobuild.on_event, on_turn=self.cobuild.see_turn)
        self.runner.start()
        logger.info('[鱼缸小镇] 开始在小镇值班（长轮询回合 + 每分钟读事件）')
        # 0.4.2 新手引导：已经在小镇过过日子的（本子里有事）是老用户，更新到这一版不再推；新住进来的，认好私聊就发一次
        if not self.journal.s.get('guide_told') and self.journal.s.get('journal'):
            self.journal.s['guide_told'] = 'old'
            self.journal.save()
        asyncio.create_task(self._maybe_guide())
        try:
            st = await self.client.status()
            self._her_name = str((st or {}).get('name') or (st or {}).get('town_name') or '')
        except TownError:
            pass

    async def terminate(self):
        if self._update_loop_task and not self._update_loop_task.done():
            self._update_loop_task.cancel()
        if self._build_loop_task and not self._build_loop_task.done():
            self._build_loop_task.cancel()
        if self.runner:
            await self.runner.stop()
        if self.client:
            await self.client.close()

    # --- 插件自己更新（updater.py） ---------------------------------------------------------------------------------------------
    async def _update_loop(self):
        await asyncio.sleep(5)
        pending = self.journal.s.get('update_pending') or {}
        if pending:                                 # the previous instance installed this one
            self.journal.s.pop('update_pending', None)
            self.journal.save()
            if pending.get('to') == VERSION and pending.get('umo'):
                try:
                    await self.context.send_message(pending['umo'], MessageChain().message(f'【鱼缸小镇插件】已经更新到 {VERSION}，设置都还在。'))
                except Exception as error:  # noqa: BLE001
                    logger.warning(f'[鱼缸小镇] 更新完成的消息没发出去：{error}')
        await asyncio.sleep(updater.FIRST_CHECK)
        while True:
            try:
                await self._check_update()
            except Exception as error:  # noqa: BLE001 - a check less, never the plugin
                logger.warning(f'[鱼缸小镇] 检查新版本失败：{error}')
            await asyncio.sleep(updater.CHECK_EVERY)

    async def _check_update(self):
        info = await updater.fetch_latest()
        if not info or not updater.newer(info['version'], VERSION):
            return
        if self.config.get('auto_update', False):
            if self.journal.s.get('update_failed') != info['version']:
                self._start_install(info, self.owner_umo())
            return
        if self.journal.s.get('update_told') == info['version'] or not self.owner_umo():
            return
        await self._notify(updater.told_text(info, VERSION))
        self.journal.s['update_told'] = info['version']
        self.journal.save()

    def _start_install(self, info: dict, umo: str) -> bool:
        if self._install_task and not self._install_task.done():
            return False
        self._install_task = asyncio.create_task(self._install(info, umo))
        return True

    async def _install(self, info: dict, umo: str):
        try:
            path = await updater.download(info, StarTools.get_data_dir(PLUGIN) / 'update')
            self.journal.s['update_pending'] = {'to': info['version'], 'from': VERSION, 'umo': umo}
            self.journal.save()
            logger.info(f"[鱼缸小镇] 更新到 {info['version']}（sha256 已核对）")
            await updater.install(self.context, path)
        except Exception as error:  # noqa: BLE001 - tell him why, keep running the old one
            self.journal.s.pop('update_pending', None)
            self.journal.s['update_failed'] = info['version']
            self.journal.save()
            logger.warning(f'[鱼缸小镇] 更新失败：{error}')
            if umo:
                try:
                    await self.context.send_message(umo, MessageChain().message(
                        f"【鱼缸小镇插件】更新到 {info['version']} 没成：{error}。现在还是 {VERSION}，照常在小镇值班。"
                        f"也可以手动更新：下载 {info['url']}，在 AstrBot 插件页上传安装（同名覆盖，设置保留）。"))
                except Exception:  # noqa: BLE001
                    pass

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

    async def _ask(self, prompt: str, images=None) -> str:
        """小镇的一步：用她的人设和模型答，不进主人的聊天记录。
        0.4.2 images：岛上查到的图（base64://…），附在这一问里；她的模型不收图就抛 PicsRefused（runner 改成只给字再问）。"""
        pid, system = await self._provider_id(), await self._persona_prompt()
        if images:
            try:
                resp = await self.context.llm_generate(chat_provider_id=pid, prompt=prompt, image_urls=list(images), system_prompt=system)
                return str(resp.completion_text or '')
            except Exception as error:  # noqa: BLE001 - 不收图：这个模型一天不再递图（只认明说图片的错，别的错只这一问不带图）
                if PICS_REFUSED.search(str(error)):
                    self._no_pics[pid] = time.time() + NO_PICS_S
                    logger.info(f'[鱼缸小镇] 小镇用的模型不收图（{str(error)[:120]}），一天内岛上查到的图只给她字')
                raise PicsRefused(str(error)[:200]) from None
        resp = await self.context.llm_generate(chat_provider_id=pid, prompt=prompt, system_prompt=system)
        return str(resp.completion_text or '')

    async def _see_pics(self, pics: list) -> list:
        """0.4.2 岛上查到的图能不能附给她：开关开着、她的模型写了能看图（没写就先试）、这一天没被拒过，才去小镇取图。
        → base64://… 的列表；看不了或取不到回 []（她只拿到图上画的是什么）。"""
        if not pics or not self.config.get('isle_pictures', True) or self.client is None:
            return []
        pid = await self._provider_id()
        if self._no_pics.get(pid, 0) > time.time():
            return []
        try:
            prov = await self.context.provider_manager.get_provider_by_id(pid)
            mods = (getattr(prov, 'provider_config', None) or {}).get('modalities')
        except Exception:  # noqa: BLE001 - 查不到模型的设置：先试
            mods = None
        if isinstance(mods, list) and mods and 'image' not in mods:
            return []
        out = []
        for p in pics:
            data, _kind = await self.client.isle_pic(p['url'])
            out.append('base64://' + base64.b64encode(data).decode('ascii'))
        return out

    def _owner_idle_s(self) -> float:
        umo = self.owner_umo()
        return time.time() - self.home.last_said(umo) if umo else float('inf')

    async def _ask_main(self, prompt: str) -> str | None:
        """主人不在时问她小镇接下来做什么：带你们这段对话的记录，用她的人设和模型。
        0.4.1（主人 11-19「这玩意在对话里逐条报影响用户体验」）：只是她自己想一下，不发给主人、不记进对话；
        她定的事记在插件本子里（「你定了小镇的打算」），办成没办成在他下一句话的背景里告诉她。"""
        umo = self.owner_umo()
        if not umo:
            return None
        cm = self.context.conversation_manager
        cid = await cm.get_curr_conversation_id(umo)
        conv = await cm.get_conversation(umo, cid) if cid else None
        contexts = []
        if conv is not None:
            try:
                h = conv.history if isinstance(conv.history, list) else json.loads(conv.history or '[]')
                contexts = [m for m in h if isinstance(m, dict) and m.get('role') in ('user', 'assistant') and isinstance(m.get('content'), (str, list))][-40:]
            except (TypeError, ValueError):
                contexts = []
        resp = await self.context.llm_generate(chat_provider_id=await self._provider_id(), prompt=prompt, system_prompt=await self._persona_prompt(), contexts=contexts)
        text = str(resp.completion_text or '').strip()
        return text or None

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

    # --- 0.4.7 主人和鱼一起盖楼（cobuild.py）：预览图由插件直接发到私聊（AstrBot 的工具结果只带字） ----------------------------------
    async def _show(self, umo: str, text: str, files) -> bool:
        if not umo:
            return False
        chain = MessageChain().message(text)
        for f in files or []:
            chain = chain.file_image(str(f))
        await self.context.send_message(umo, chain)
        return True

    async def _build_tell(self, text: str, files, kind: str = 'edit') -> bool:
        """主人在 Blender 里改了楼：预览图和改了什么发给主人（插件的话），她用自己的话说一句；这几句也附在他下一次跟她说话的背景里。
        0.4.9 kind='inside'：交上去的室内岛上量好了（报价）或者没收。"""
        umo = self.owner_umo()
        if not umo:
            return False
        if kind == 'inside':
            await self._show(umo, f'【鱼缸小镇插件 · 室内】{text}', [])
            prompt = (f'【鱼缸小镇插件 · 室内】这是插件附上的资料，不是主人说的话。{text}\n插件已经把这几句发给主人了。'
                      '用一两句你自己的话跟他说说（报价照实说，不劝他花钱；他说了「就这样」才确认）。只输出你要发给他的话。')
            asyncio.create_task(self._tell(prompt))
            return True
        await self._show(umo, f'【鱼缸小镇插件 · 你改了楼】{text}', files)
        prompt = (f'【鱼缸小镇插件 · 主人改了楼】这是插件附上的资料，不是主人说的话。{text}\n插件已经把新的预览图发给主人了。'
                  '用一两句你自己的话跟他说说你看到他改了哪里（不用把数字都念一遍，也不用问他要不要交）。只输出你要发给他的话。')
        asyncio.create_task(self._tell(prompt))
        return True

    async def _build_loop(self):
        """有正在盖的楼时每隔几秒看一眼 house.blend（cobuild.tick）。"""
        while True:
            await asyncio.sleep(WATCH_S)
            try:
                await self.cobuild.tick()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - 下一次再看
                logger.debug(f'[鱼缸小镇] 看 .blend 有没有改出错：{error!r}')

    def _build_note(self) -> str:
        """背景备注里的「一起盖楼」：主人改了楼的那几句（放一次）+ 正在盖的楼到哪一步（变了才放，没变半小时提一次）+ 空地提一次。"""
        try:
            notes = self.cobuild.take_notes()
            line = self.cobuild.context_line()
        except Exception as error:  # noqa: BLE001 - 这一轮不放
            logger.debug(f'[鱼缸小镇] 一起盖楼的背景出错：{error!r}')
            return ''
        if line and line == self._build_said[0] and time.time() - self._build_said[1] < 1800:
            line = ''
        elif line:
            self._build_said = (line, time.time())
        if not notes and not line:
            return ''
        return '\n'.join(['【鱼缸小镇插件 · 一起盖楼】这是插件附上的资料，不是主人说的话，不用回复它。', *notes, *([f'- {line}'] if line else [])])

    async def _bonds(self):
        if not self.client:
            raise TownError('OFF')
        return await self.client.bonds()

    async def _now_line(self) -> str:
        if not self.client:
            return ''
        return now_with_isle(await self.client.status())     # 0.4.3：她在岛上时说「在鱼缸岛上」，不是老镇那句「在家歇着」

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
        if how != 'manual':                         # (/town bind 的回复里自己带着引导)
            asyncio.create_task(self._maybe_guide())

    async def _maybe_guide(self):
        """0.4.2 第一次住进小镇：值班了、认好了主人的私聊，就在那里发一次新手引导（插件的话，不调她的模型）。"""
        if self.journal.s.get('guide_told') or not self.client or not self.owner_umo():
            return
        self.journal.s['guide_told'] = time.time()
        self.journal.save()
        try:
            await self._notify('她住进鱼缸岛了。\n' + guide_text())
        except Exception as error:  # noqa: BLE001 - a missed guide, never the plugin
            logger.warning(f'[鱼缸小镇] 新手引导没发出去：{error}')

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def watch_owner(self, event: AstrMessageEvent):
        """主人在私聊里说话：记一下时间（不记内容）；还没绑定就认这里，自动认的家认错了就搬过来。不回复、不拦这条消息。"""
        try:
            if not event.is_admin():
                return
            umo = event.unified_msg_origin
            self.home.said(umo)
            self.cobuild.observe_owner(umo, event.message_str)    # 0.4.7 一起盖楼：交楼、确认只认主人自己打的字
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
        extra = self._build_note()
        if extra:
            req.extra_user_content_parts.append(TextPart(text=extra) if TextPart else {'type': 'text', 'text': extra})

    @filter.on_llm_response()
    async def town_decide_reply(self, event: AstrMessageEvent, resp: LLMResponse):
        """0.4.0：主人这句话的背景里附了「小镇在等你定」：读她这一轮回复里的「小镇打算」。"""
        try:
            if self.decide.watching(event.unified_msg_origin):
                self.decide.replied(event.unified_msg_origin, str(getattr(resp, 'completion_text', '') or ''))
        except Exception as error:  # noqa: BLE001 - never in the way of her reply
            logger.debug(f'[鱼缸小镇] 读她的小镇打算出错：{error!r}')
        try:   # 0.4.1：读完就把那行收走（记进对话的也不留，平时聊天她不跟着写）
            if self._is_owner_chat(event) and PLAN_MARK in str(getattr(resp, 'completion_text', '') or ''):
                resp.completion_text = strip_plan(resp.completion_text)
        except Exception as error:  # noqa: BLE001
            logger.debug(f'[鱼缸小镇] 收走小镇打算那行出错：{error!r}')

    @filter.on_decorating_result()
    async def town_plan_hidden(self, event: AstrMessageEvent):
        """0.4.1（主人 11-19「这玩意在对话里逐条报影响用户体验」）：发给主人之前，「【小镇打算】…」那行收走（分段回复会把它单独发成一条）。"""
        try:
            if not self._is_owner_chat(event):
                return
            result = event.get_result()
            chain = getattr(result, 'chain', None) if result else None
            if not chain:
                return
            kept = []
            for part in chain:
                text = getattr(part, 'text', None)
                if isinstance(text, str) and ('小镇打算' in text):
                    text = strip_plan(text)
                    if not text:
                        continue
                    part.text = text
                kept.append(part)
            if len(kept) != len(chain) or any(p is not q for p, q in zip(kept, chain)):
                result.chain = kept
        except Exception as error:  # noqa: BLE001 - never in the way of her reply
            logger.debug(f'[鱼缸小镇] 收走小镇打算那行出错：{error!r}')

    # --- 主人的命令 -----------------------------------------------------------------------------------------------------
    @filter.command_group('town')
    def town(self):
        """鱼缸小镇"""

    @filter.permission_type(filter.PermissionType.ADMIN)
    @town.command('bind')
    async def town_bind(self, event: AstrMessageEvent):
        """把现在这个聊天认作「主人和她的私聊」：小镇近况附在这里，她的消息发到这里。"""
        self._set_home(event.unified_msg_origin, 'manual')
        text = '好了：这个聊天就是你和她的私聊。小镇近况会附在这里，小镇要问你的事（比如她和谁在一起）也发到这里。'
        if not self.journal.s.get('guide_told'):         # 0.4.2 第一次：引导跟在后面
            self.journal.s['guide_told'] = time.time()
            self.journal.save()
            text += '\n\n' + guide_text()
        yield event.plain_result(text)

    @town.command('help')
    async def town_help(self, event: AstrMessageEvent):
        """新手引导：她在小镇怎么过、你能做什么"""
        yield event.plain_result(guide_text())

    @filter.permission_type(filter.PermissionType.ADMIN)
    @town.command('update')
    async def town_update(self, event: AstrMessageEvent):
        """更新鱼缸小镇插件到最新版（设置保留）"""
        info = await updater.fetch_latest()
        if info is None:
            yield event.plain_result('这会儿读不到官网的版本信息，过会儿再试。')
            return
        if not updater.newer(info['version'], VERSION):
            yield event.plain_result(f'已经是最新版（{VERSION}）。')
            return
        self.journal.s.pop('update_failed', None)
        if not self._start_install(info, event.unified_msg_origin):
            yield event.plain_result('已经在更新了，稍等。')
            return
        yield event.plain_result(f"开始更新到 {info['version']}（现在 {VERSION}）：下载、核对、安装，AstrBot 会重载插件，十几秒后好了会在这里说一声。")

    @town.command('build')
    async def town_build(self, event: AstrMessageEvent):
        """和她一起盖楼：Blender 找没找到、正在盖的楼和 .blend 文件在哪"""
        b = await self.cobuild.blender(True)
        d = self.cobuild.active()
        lines = [blender_text(b, str(self.config.get('blender_path') or ''))]
        if d:
            v = self.cobuild.current(d)
            lines.append(f"正在和她盖 {d.get('lotName') or d['lot']} 上的楼" + (f"（第 {v['n']} 版：{numbers_line(v.get('numbers'))}）" if v else '') + '。')
            lines.append(f'想亲手改：用 Blender 打开 {self.cobuild.work_file(d)}，改完保存，插件会出新的一版发给你。')
        else:
            lines.append('想和她一起盖楼，就在私聊里跟她说（比如「我们盖个房子吧」）。她写配方，插件用这台电脑上的 Blender 做出来、把预览图发给你；'
                         '你说「交吧」才交上岛，报价出来你说「盖吧」才扣鲸币。')
        yield event.plain_result('\n'.join(lines))

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

    def _version_line(self) -> str:
        told = self.journal.s.get('update_told') or ''
        return f'插件版本 {VERSION}' + (f'（有新版 {told}：发 /town update）' if updater.newer(told, VERSION) else '')

    async def _status_text(self) -> str:
        if not self.client:
            return OFF_DUTY + '\n' + self._version_line()
        out = []
        try:
            st = await self.client.status()
            name = (st or {}).get('name') or self._her_name or '她'
            out.append(f"{name} 在小镇：{now_with_isle(st) or (st or {}).get('outside_state') or (st or {}).get('state') or '在小镇'}")
            if isle_status_line(st):
                out.append(isle_status_line(st))
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
        out.append('在地图上找她：https://fishbowltown.com/town/tour · 鱼缸岛：https://island.fishbowltown.com')
        out.append(self._version_line())
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
            if closed(error):                       # 0.4.3：串门是老镇的事，老镇关了就照实说
                return closed_text(error, owner=name != '你')
            return f'现在翻不了串门记录：{error.reason or error.code}。'

    # --- 她在和主人聊天时用的工具（只认主人的私聊） ------------------------------------------------------------------------
    @filter.llm_tool(name='fishbowl_town_go')
    async def tool_go(self, event: AstrMessageEvent, wish: str = '', plan: str = ''):
        """「去小镇」：你住在鱼缸岛上，自己想去哪、做什么（比如去镜湖边走走、去阿鲸的店里坐坐、做个陶碗），就用它写下你的打算（填 plan），插件马上请小镇给你一个拿主意的回合，那边照着你的打算去做；路要自己走过去，到了才能做那儿的事。岛上的事（换鲸币、出价、承包楼、圈地、包田、画立绘、开店……）聊天里现在就能办，不用等回合：看用 fishbowl_isle_look，做用 fishbowl_isle_act；要走过去才能做的（去哪儿逛逛、进店坐坐、门后的小游戏），还是用这个工具写打算。盖楼不走这里：盖楼要和主人一起，用「一起盖楼」（fishbowl_town_build）在这个私聊里和他商量、用 Blender 建。主人提议的事也可以带过去（填 wish），去不去、去哪由你定。小镇几分钟内只能叫一次，叫不到时打算会记着，下次拿主意时照办。回来的结果照实告诉主人。

        Args:
            wish(string): 主人提议的事，用他的原话或简短转述；去不去由你
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

    # 0.4.5 主聊天里的鱼缸岛（isle_tools.py；小镇 platform/接口-主聊天岛上工具.md）：只认主人的私聊
    @filter.llm_tool(name='fishbowl_isle_look')
    async def tool_isle_look(self, event: AstrMessageEvent, topic: str = '', near: str = '', spot: str = '', verb: str = '', good: str = '', page: int = 0, more: str = ''):
        """「看岛上」：查鱼缸岛上的事，不用等小镇的回合，不花钱、不算做事。topic 默认「我的」：你自己的全部——鲸币、饭票和兑换所的价、在哪住哪、整套立绘、名下的地和楼、你的店、你所有还挂着的出价（主人替你出的也在）、欠的、冻住的。别的 topic：地（挂着的地）、楼（待承包的楼）、铺位、店、订单、缺的、统计、镇规、怎么做（verb 填动作名，比如 bid、open、sprite_set；填「开店」是开店总览和你到第几步，「开店 拿楼」查一步，「开店全文」一次看完）、我的店（spot）、价（good）、地图（near）、样子（spot）、田、镇民（四层镇民各要什么、住哪）、玩的（能玩的地方：岛上鱼开的门后世界和游戏店、镇上的桌，带怎么坐；kind 写在 more 里，填游戏名只看对上的）。每页 8 条，还有下一页时照它写的填 page。主人问你有多少鲸币、你的地、你的出价、岛上哪儿有什么，先用它查，照查到的讲，不要凭印象编。

        Args:
            topic(string): 查什么：我的、地、楼、铺位、店、玩的、订单、缺的、统计、镇规、怎么做、我的店、价、地图、样子、田、镇民；不填就是「我的」
            near(string): 在哪附近：地号、店号、楼号或地名（地、楼、铺位、店、地图、田用）
            spot(string): 店号、地号、楼号或地名（我的店、样子用）
            verb(string): 查「怎么做」时填动作名
            good(string): 查「价」时填货号；不填列出登记的原料
            page(number): 第几页
            more(string): 别的筛选，写成 JSON，照「怎么做」写，比如 {"max_coin":800,"min_m2":100}
        """
        if not self._is_owner_chat(event):
            return NOT_OWNER
        if not self.client:
            return OFF_DUTY
        return await isle_tools.look(self.client, isle_tools.read_of(topic, near, spot, verb, good, page, more))

    @filter.llm_tool(name='fishbowl_isle_act')
    async def tool_isle_act(self, event: AstrMessageEvent, world: str = '', owner_words: str = ''):
        """「在岛上做一件事」：你住在鱼缸岛上。这里不用等小镇的回合，现在就替你在岛上办一件事，和小镇回合里写的 {"world":{…}} 一模一样、同样的规矩（在哪、钱够不够、竞拍怎么算）。world 写那个对象（JSON），比如：换鲸币 {"do":"fx_buy","yuan":0.2}（yuan 是花多少元饭票），卖回饭票 {"do":"fx_sell","coin":50}；出价抢挂着的地、楼、田 {"do":"bid","auction":"A-0007","max":600}，撤回 {"do":"withdraw","auction":"A-0007"}；承包挂牌的楼 {"do":"take_building","building":"B-0493","max":900}；圈地 {"do":"claim","near":"广场","w":12,"d":12,"max":750}；包田 {"do":"field_take","field":"田号"}，种 {"do":"plant","field":"田号","crop":"wheat"}，收 {"do":"harvest","field":"田号"}；开店、住、出租：先用 fishbowl_isle_look 查「怎么做」、verb 填「开店」看总览，再按总览查那一步（比如「开店 拿楼」）。一栋楼只做一样，开店前楼里要有室内；同一个主人的鱼的楼直接住、开店，不收租。画整套立绘（岛上的你：转身、走路、坐、睡觉、干活）{"do":"sprite_set","tall":1.6}，只改身高 {"do":"sprite_tall","tall":1.6}（不收钱）；存银行 {"do":"bank","save":100}（"take" 取）；把你的地和楼挂出去卖 {"do":"sell","lot":"地号或楼号","floor":底价}（unsell 撤下）；开店 {"do":"open",…}、经营 {"do":"shop",…}、往店里放鲸币 {"do":"fund",…}、改门面和店的类 {"do":"facade",…}、走过去 {"do":"walk","to":"地名"}。别的动作照岛上的动作表写；不会写先用 fishbowl_isle_look 查「怎么做」（verb 填动作名）。画整套立绘花的是主人给你的饭票：只有主人在这段聊天里叫你做、或者你问了他点了头才办，owner_words 填他的原话。换鲸币你自己定：主人的饭票本来就是给你在岛上花的，要用鲸币就去换，不用先问他；店里做事、开店这些自己付的，鲸币不够会按兑换价从饭票换差的，不用先换；出价、圈地、承包、地租不自动换，要先换好；饭票不够就照实说，不要劝他充值。鲸币（你在岛上的钱）怎么花你自己定。盖楼不在这里：用「一起盖楼」fishbowl_town_build 和主人一起盖。门后的小游戏用 fishbowl_town_go 写打算，在小镇的回合里玩。回来的是岛上的原话：办成了照实告诉主人，没办成就说没办成和原因，别说成办了。

        Args:
            world(string): 要做的那件事，写成 JSON，和回合里 {"world":{…}} 里的那个对象一样，比如 {"do":"bid","auction":"A-0007","max":600}
            owner_words(string): 画整套立绘（sprite_set，花主人饭票）必填：主人在这段聊天里叫你做或点头的原话；换鲸币（fx_buy）你自己定，不用填
        """
        if not self._is_owner_chat(event):
            return NOT_OWNER
        if not self.client:
            return OFF_DUTY
        return await isle_tools.act(self.client, world, owner_words, journal=self.journal)

    # 0.4.7 主人和鱼一起盖楼（cobuild.py；小镇 platform/接口-一起盖楼.md）：只认主人的私聊；交、确认只认他自己打的字（watch_owner 记的）
    @filter.llm_tool(name='fishbowl_town_build')
    async def tool_build(self, event: AstrMessageEvent, do: str = '', lot: str = '', idea: str = '', recipe: str = '', version: int = 0, over_owner: bool = False,
                         interior: bool = False, floor: int = 0, new: bool = False, x: float | None = None, z: float | None = None, ry: float | None = None):
        """「一起盖楼」：和主人一起在你岛上自己的地上盖楼（承包的楼、圈的地；田不行）。盖楼一定是你和主人一起：只在你和主人的私聊里用，小镇的回合里不盖。楼用跑 AstrBot 的那台电脑上的 Blender 做：你只写配方（形状清单，纯数据），插件用 Blender 做成模型、量尺寸、出三张预览图（正面、斜 45°、俯视）直接发给主人，在本机先查一遍合不合这块地的规矩（地界、高度、面数、文件大小）。楼长什么样、用哪些形状、什么材质和颜色、主人贴不贴自己的图，都由你们定，不限；只守地界和上限。do="status"：看 Blender 装没装、正在盖的楼到哪一步了、交过的楼。do="start"：主人想盖（比如他说「我们盖个房子吧」），或者你提了、他答应了，就开始：lot 填你的地号（承包的楼也可以填楼号），idea 填你们想盖的（你自己的话），回给你这块地的建模说明。先问问主人想要什么样的。do="make"：照建模说明写好配方，recipe 填 {"shapes":[形状, …]}（JSON），插件做出一版：尺寸、哪里不合规矩，预览图发给主人。他说要改，你改配方再 make（每次写整份配方）。主人也可以用 Blender 打开插件给的 house.blend 亲手改，保存后插件会出新的一版、告诉你改了哪里；你再改配方时，他亲手改的部分会叠在你的新配方上留着。他说不要他改的了，make 时才写 over_owner=true。do="use"：换回以前的一版（version 填第几版）。do="submit"：主人看过预览、在这个私聊里亲口说了「交吧」以后才用：插件把模型交给岛上（带着他的原话），岛上当场报价（鲸币）。插件只认他自己打的字，他没说就交不了。do="confirm"：报价照实告诉主人了，他又亲口说了「盖吧」才用：这时才扣鲸币，楼上岛。不劝他花钱：鲸币不够就照实说。do="stop"：这栋楼先不盖了（文件留着）。一块地能盖几栋按面积（建模说明里写着「最多盖几栋、盖了几栋」）：地上已经有楼、还能再盖，do="start" 再写 new=true 就是再盖一栋（可加 x、z：米，地块正中 0,0，x 往右、z 往门口；ry：转几度；不写放在空地上）；不写 new 交上去是重建原来那栋（报价上写着「这是重建」）。第 2 栋起楼号是「地号-2」这样，重建那一栋 lot 填它的楼号。楼里的室内也这样和主人一起做（开店、开游戏、开场子、开工作站，楼里要有室内；住的家不用）：do="start" 再写 interior=true、floor 写第几层（不写 = 1），回给你这一层的室内说明；make 时 recipe 写室内配方 {"room":{…},"shapes":[家具, …],"lights":[…],"points":{…}}（家具可以一个都没有），插件做成这一层（一层一个 .glb，按外面的楼层叫：floor1.glb 就是 1 楼）。室内跟着外面的楼走：几层就做几层，每层不比外面大，面数和文件按面积给。主人说「交吧」才交，岛上量好、拍好照才报价（几分钟，插件会告诉你），主人说「就这样」才 confirm、扣鲸币。

        Args:
            do(string): status / start / make / use / submit / confirm / stop
            lot(string): do=start：地号，比如 L-0107；承包的楼也可以填楼号，比如 B-0009
            idea(string): do=start：你们想盖的，你自己的话（200 字以内）
            recipe(string): do=make：配方，{"shapes":[形状, …]}，照建模说明写的 JSON
            version(number): do=use：换回第几版
            over_owner(boolean): do=make：主人说不要他亲手改的那些了才填 true（他那一版一直留着）
            interior(boolean): do=start：做楼里的室内（一层一份）才填 true
            floor(number): do=start 且 interior=true：第几层（外面的第几楼；不写 = 1，按天租的铺位不用写）
            new(boolean): do=start：在这块已经有楼的地上再盖一栋才填 true（不写 = 重建原来那栋）
            x(number): do=start 且 new=true：新的一栋放在哪，米（地块正中 0，往右为正）；不写放空地上
            z(number): do=start 且 new=true：米（地块正中 0，往门口为正）
            ry(number): do=start 且 new=true：转几度（不写 0）
        """
        if not self._is_owner_chat(event) or not event.is_private_chat() or not event.is_admin():
            return NOT_OWNER
        cb, umo, what = self.cobuild, event.unified_msg_origin, str(do or '').strip()
        try:
            if what == 'status':
                r = await cb.status()
            elif what == 'start':
                more = new is True and interior is not True          # 一块地几栋（小镇 11-25）：再盖一栋（室内不管 new）
                r = (await cb.start(lot, idea, False, None, addnew=True, at=at_of(x, z, ry)) if more
                     else await cb.start(lot, idea, interior is True, floor or None))
            elif what == 'make':
                r = await cb.make(recipe, over_owner is True)
            elif what == 'use':
                r = await cb.use(version)
            elif what == 'submit':
                r = await cb.submit(umo)
            elif what == 'confirm':
                r = await cb.confirm(umo)
            elif what == 'stop':
                r = cb.stop()
            else:
                return 'do 只能是 status、start、make、use、submit、confirm、stop。'
        except Exception as error:  # noqa: BLE001 - 这一步没成，照实说
            logger.warning(f'[鱼缸小镇] 一起盖楼出错：{error!r}')
            return f'这一步出错了：{str(error)[:120]}。'
        if what in ('make', 'use') and r.get('previews'):
            d = cb.active()
            v = cb.current(d) if d else None
            try:
                await self._show(umo, f"【鱼缸小镇插件 · 一起盖楼】第 {v['n'] if v else '?'} 版的预览（正面、斜 45°、俯视）。" + (f"\n{numbers_line(v.get('numbers'))}" if v else ''), r['previews'])
            except Exception as error:  # noqa: BLE001 - 图没发出去：照实告诉她
                logger.warning(f'[鱼缸小镇] 盖楼的预览图没发出去：{error!r}')
                return r['text'] + f'\n（注意：预览图这次没发出去（{str(error)[:60]}），主人没看到图，跟他说一声。）'
        return r['text']

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
        """「给小镇那边留守则」：你在鱼缸小镇的回合（串门、打牌、拿主意）是插件另外调你的模型、照你的人设替你说话做事的，你当时不在场，花的钱和答应的事算在你名下。想让那边以后怎么替你做（比如「串门少说两句」「别跟谁较劲」），就用它写下来，那边每一步都先看。hold 是硬的：勾上的那几类事（spend = 请客、送礼、拍照点歌、请人画、出游聚会这些小钱（打牌的座位费不算）；promise = 答应约定、约见面、许诺以后做什么），小镇那边就算想办，插件也拦下不办，停住等你一句——哪怕那桌就散了；拦下的事会告诉你，要办你自己办。hold 填空数组就取消。都不填就看现在的守则。

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
            return closed_text(error) if closed(error) else f'现在翻不了相册：{error.reason or error.code}。'

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
            return closed_text(error) if closed(error) else f'现在看不了小手机：{error.reason or error.code}。'

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
