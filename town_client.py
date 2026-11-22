"""小镇「外来的鱼」接口的客户端（/town/api/outside/，Authorization: Bearer fbo_…）。
接口文档：https://fishbowltown.com/town/docs/outside

0.4.5：外来的鱼有了自己的入口机 api.fishbowltown.com（一万条鱼的长轮询不再挤在主站那台上），路径、接口都一样。
默认地址（没填，或者填的就是默认的——0.4.4 以前的默认 fishbowltown.com、更早的 fish.bside-moon.cn 也算，AstrBot 会把默认值存进配置）：走入口机；
入口机连着 2 次连不上（DNS、拒绝连接、TLS、没等到任何回应就超时；小镇回了 4xx / 5xx / 503 TOWN_RESTARTING 不算），
先改走主站 10 分钟，再试入口机。自己填了别的地址的：一直只连那个地址。"""
from __future__ import annotations

import json
import re
import time
from urllib.parse import urlsplit

import aiohttp

API_BASE = 'https://api.fishbowltown.com/town/api/outside/'
LEGACY_BASE = 'https://fishbowltown.com/town/api/outside/'
OLD_BASE = 'https://fish.bside-moon.cn/town/api/outside/'       # 更早的默认（主人 11-22：也算默认，一起搬到入口机）
DEFAULT_BASE = API_BASE
FALLBACK_AFTER = 2              # 入口机连着这么多次连不上，就改走主站
FALLBACK_S = 10 * 60            # 走主站这么久，再试入口机
SECRET = re.compile(r'(fbo_[A-Za-z0-9_-]{6,}|\bsk-[A-Za-z0-9_-]{8,})')
PIC_PATH = re.compile(r'/isle/pic/[0-9a-f]{12}\.(jpg|png)$')
PIC_BYTES = 1_500_000
# 长轮询时告诉小镇这个程序还答哪几种回合（小镇 outside_turns.py advertise()）
TURN_KINDS = 'activity,build'


def is_default_base(base) -> bool:
    """配置里的地址算不算「默认」：没填、新的默认、0.4.4 以前的默认、更早的 fish.bside-moon.cn（存进配置里的也算）。"""
    b = str(base or '').strip()
    if not b:
        return True
    return (b if b.endswith('/') else b + '/') in (API_BASE, LEGACY_BASE, OLD_BASE)


def redact(s) -> str:
    return SECRET.sub('***', str(s or ''))


class TownError(Exception):
    def __init__(self, code: str, status: int = 0, reason: str = '', retry_after: float = 0, data=None):
        super().__init__(f'{code}: {reason}' if reason else code)
        self.code, self.status, self.reason, self.retry_after, self.data = code, status, reason, retry_after, data


def transient(code: str, path: str = '') -> bool:
    """小镇重启、网络抖一下时会出现的错：不停地重连，不打扰主人。
    0.4.3：TOWN_OFF 只在回合、事件、状态这些一直都在的接口上算「一会儿就好」；老镇那几块（串门记录、相册、关系……）回 TOWN_OFF 是那地方关了，不重试。"""
    if code == 'TOWN_OFF' and path and not core_path(path):
        return False
    return code in ('NETWORK', 'UNKNOWN', 'TOWN_OFF', 'TOWN_RESTARTING') or bool(re.fullmatch(r'HTTP_5\d\d', code))


# 一直都在的接口（她住在鱼缸岛上也照样用）。别的是老镇那几块：11-28 老镇关门以后会回 TOWN_OFF 或 404
CORE = ('turns', 'events', 'status', 'agency', 'settings', 'consent', 'isle/')
CLOSED_S = 30 * 60          # 老镇的一块回了「关了」：这么久里不再去问（不是一直重试），之后再看一眼


def core_path(path: str) -> bool:
    p = str(path or '').lstrip('/')
    return any(p == c or p.startswith(c if c.endswith('/') else c + '/') for c in CORE)


def closed(error) -> bool:
    """老镇那一块关了（或这个小镇没有这一项）：TOWN_OFF / 没有这个地址，都是 404。"""
    return isinstance(error, TownError) and error.status == 404 and error.code in ('TOWN_OFF', 'HTTP_404')


class TownClient:
    def __init__(self, code: str, base: str = DEFAULT_BASE, user_agent: str = 'astrbot-fishbowl-town', session: aiohttp.ClientSession | None = None,
                 log=None, now=time.monotonic):
        if not isinstance(code, str) or not code.strip():
            raise TownError('NO_CODE', 0, '还没填小镇接入码')
        self.auto = is_default_base(base)            # 默认地址：入口机，连不上时退回主站
        b = str(base or '').strip()
        self.fixed = API_BASE if self.auto else (b if b.endswith('/') else b + '/')
        self.log, self.now = log, now
        self.fails = 0                                # 入口机连着几次连不上
        self.fallback_until = 0.0                     # 到这个时间前走主站
        self.auth = 'Bearer ' + code.strip()
        self.ua = user_agent
        self._session = session
        self._own = session is None
        self.shut: dict = {}        # 0.4.3 老镇那一块 -> (到什么时候前不再去问, 那次的错)

    # --- 0.4.5 入口机 / 主站 ----------------------------------------------------------------------------------------------
    def _say(self, msg: str):
        try:
            if self.log is not None:
                self.log.info(f'[鱼缸小镇] {msg}')
        except Exception:  # noqa: BLE001 - 只是一行日志
            pass

    @property
    def root(self) -> str:
        """这一次请求用哪个地址。走主站满 10 分钟：改回入口机（说一声）。"""
        if not self.auto:
            return self.fixed
        if self.fallback_until:
            if self.now() < self.fallback_until:
                return LEGACY_BASE
            self.fallback_until, self.fails = 0.0, 0
            self._say('改回小镇的接口入口 api.fishbowltown.com（走主站 fishbowltown.com 满 10 分钟了）')
        return API_BASE

    def _reached(self, at: str):
        """入口机回话了（不管回的是什么状态码）：连不上的次数清零。"""
        if self.auto and at == API_BASE:
            self.fails = 0

    def _unreachable(self, at: str):
        """入口机连不上（网络层：还没收到任何回应）：连着 FALLBACK_AFTER 次就改走主站。"""
        if not self.auto or at != API_BASE or self.fallback_until:
            return
        self.fails += 1
        if self.fails < FALLBACK_AFTER:
            return
        self.fails = 0
        self.fallback_until = self.now() + FALLBACK_S
        self._say(f'小镇的接口入口 api.fishbowltown.com 连着 {FALLBACK_AFTER} 次连不上，先改走主站 fishbowltown.com，{FALLBACK_S // 60} 分钟后再试入口机')

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._own = True
        return self._session

    async def close(self):
        if self._own and self._session is not None and not self._session.closed:
            await self._session.close()

    async def request(self, method: str, path: str, *, query: dict | None = None, body=None, timeout: float = 15, form=None):
        if core_path(path):
            try:
                return await self._request(method, path, query=query, body=body, timeout=timeout, form=form)
            except TownError as error:          # 小镇在重启 / 连不上：重启以后各块可能又开了，记着的「关了」作废
                if error.code == 'NETWORK' or error.status >= 500:
                    self.shut.clear()
                raise
        part = path.split('/')[0]
        was = self.shut.get(part)
        if was and was[0] > time.monotonic():            # 刚说过关了：不再去问
            raise TownError(was[1].code, was[1].status, was[1].reason, 0, was[1].data)
        try:
            return await self._request(method, path, query=query, body=body, timeout=timeout, form=form)
        except TownError as error:
            if closed(error):
                self.shut[part] = (time.monotonic() + CLOSED_S, error)
            raise

    async def _request(self, method: str, path: str, *, query: dict | None = None, body=None, timeout: float = 15, form=None):
        params = {k: str(v) for k, v in (query or {}).items() if v is not None}
        headers = {'Authorization': self.auth, 'User-Agent': self.ua, **({'Content-Type': 'application/json'} if body is not None and form is None else {})}
        # form：multipart（0.4.7 交楼的 glb），fields = [(名字, 值, 文件名, 类型)]；每次请求现做一份（aiohttp 的 FormData 只能发一次）
        data = None
        if form is not None:
            data = aiohttp.FormData()
            for name, value, filename, ctype in form:
                if filename:
                    data.add_field(name, value, filename=filename, content_type=ctype)
                else:
                    data.add_field(name, str(value))
        elif body is not None:
            data = json.dumps(body, ensure_ascii=False).encode()
        http = await self._http()
        root = self.root
        answered = False                              # 收到回应了没有（收到以后再断的不算入口机连不上）
        try:
            async with http.request(method, root + path, params=params, headers=headers, data=data,
                                    timeout=aiohttp.ClientTimeout(total=timeout)) as res:
                answered = True
                self._reached(root)
                text = await res.text()
                status, retry = res.status, res.headers.get('Retry-After')
        except aiohttp.ClientError as error:
            if not answered:
                self._unreachable(root)
            raise TownError('NETWORK', 0, redact(error)) from None
        except TimeoutError:
            if not answered:
                self._unreachable(root)
            raise TownError('NETWORK', 0, 'timeout') from None
        try:
            data = json.loads(text) if text else None
        except ValueError:
            data = None
        if status >= 400:
            code = data.get('error') if isinstance(data, dict) and isinstance(data.get('error'), str) else f'HTTP_{status}'
            try:
                ra = float((data or {}).get('retry_after') if isinstance(data, dict) and data.get('retry_after') is not None else retry or 0)
            except (TypeError, ValueError):
                ra = 0
            raise TownError(code, status, redact((data or {}).get('reason') if isinstance(data, dict) else ''), min(ra, 120) if ra > 0 else 0,
                            data if isinstance(data, dict) else None)
        return data

    # 长轮询：有回合立刻回，没有最多等 wait 秒。kinds=activity：她在码头、电玩城、小店……说的话也交给她自己写；
    # 0.4.4 build：鱼缸岛的建模回合（她自己用模型把楼写成形状清单，build.py）。不带 build 的程序收不到建模回合
    async def turns(self, wait: int = 25):
        return await self.request('GET', 'turns', query={'wait': wait, 'kinds': TURN_KINDS}, timeout=wait + 10)

    async def answer(self, turn_id: str, answer: str):
        return await self.request('POST', 'turns/' + turn_id, body={'answer': answer}, timeout=10)

    async def events(self, after=None, limit: int = 50):
        return await self.request('GET', 'events', query={'after': after, 'limit': limit})

    async def status(self):
        return await self.request('GET', 'status')

    async def bonds(self):
        return await self.request('GET', 'bonds', timeout=5)

    async def agency(self):
        return await self.request('POST', 'agency', body={})

    async def consent(self, body: dict):
        return await self.request('POST', 'consent', body=body)

    async def settings(self, body: dict):
        return await self.request('POST', 'settings', body=body)

    async def album(self, limit: int = 30):
        return await self.request('GET', 'album', query={'limit': limit}, timeout=10)

    async def phone(self):
        return await self.request('GET', 'phone', timeout=8)

    async def visit_list(self, limit: int = 10):
        return await self.request('GET', 'visits', query={'limit': limit}, timeout=8)

    async def visit_lines(self, visit_id: str):
        return await self.request('GET', 'visits/' + visit_id, timeout=8)

    # 0.4.2 鱼缸岛（isle.py）：规矩按版本取一次；一回合里先查再出手
    async def isle_static(self, have: str = ''):
        """规矩全文：have = 手上的版本。总是 200：{version, same: true} 或 {version, blocks: {rules, verbs, goods, law}}。"""
        return await self.request('GET', 'isle/static', query={'v': have or None}, timeout=8)

    async def isle_read(self, turn_id: str, read: dict):
        """查一页（只读，不占行动次数）：read = {do: 'look', topic, 筛选…, page}。→ {ok, text, next, pics?}。"""
        return await self.request('POST', 'isle/read', body={'turn': turn_id, 'read': read}, timeout=8)

    # 0.4.5 主聊天里的鱼缸岛（isle_tools.py；platform/接口-主聊天岛上工具.md）：不等回合。除了接入码 / TOWN_OFF / 503，都回 200 {ok, code, text}
    async def isle_look(self, read: dict):
        """查看（和回合里的 isle/read 一样的回答，不要回合号；主聊天每小时 30 次）：read = {topic, 筛选…, page}。"""
        return await self.request('POST', 'isle/look', body={'read': read}, timeout=15)

    async def isle_act(self, world: dict, ref: str = ''):
        """现在做一件岛上的事：world 和回合里写的一样；ref = 插件起的单号（同一个 ref 岛上只办一次）。→ {ok, do, code, text, ref, receipt, detail?}。"""
        return await self.request('POST', 'isle/act', body={'world': world, **({'ref': ref} if ref else {})}, timeout=30)

    # 0.4.7 主人和鱼一起盖楼（cobuild.py；小镇 platform/接口-一起盖楼.md §1）：都回 200 {ok, code, text, …}
    async def isle_build_guide(self, lot: str):
        """这块地的建模说明 + 上限 + 给 recipe_to_blend.py 的 lot.json。→ {ok, lot, lot_name, text, limits, types, materials, lot_json}。"""
        return await self.request('GET', 'isle/build_guide', query={'lot': lot}, timeout=15)

    async def isle_build(self, lot: str, idea: str, owner_words: str, glb: bytes, door: dict | None = None):
        """交主人点头的 glb（multipart：lot、idea、owner_words、file；0.4.9 起带配方里的正门 door），岛上当场报价。→ {ok, build_id, quote{coin, upkeep_day_coin, lines…}, …}。"""
        form = [('lot', str(lot), None, None), ('idea', str(idea or ''), None, None), ('owner_words', str(owner_words or ''), None, None)]
        if isinstance(door, dict) and isinstance(door.get('side'), str):
            try:
                at = float(door.get('at') or 0)
            except (TypeError, ValueError):
                at = 0.0
            form.append(('door', json.dumps({'side': door['side'], 'at': at}, ensure_ascii=False), None, None))
        form.append(('file', bytes(glb), 'house.glb', 'model/gltf-binary'))
        return await self.request('POST', 'isle/build', form=form, timeout=120)

    # 0.4.9 一层室内（室内跟着外面的楼走：几层就做几层，每层不比外面大，面数和文件按面积给）
    async def isle_interior_guide(self, lot: str, floor: int | None = None):
        """这一层的室内说明：{ok, lot, lot_name, floor, floors, text, room_json（存成 --room 的文件）, limits, door}。"""
        return await self.request('GET', 'isle/interior_guide', query={'lot': lot, 'floor': floor if floor else None}, timeout=15)

    async def isle_interior(self, lot: str, floor: int, idea: str, owner_words: str, glb: bytes, name: str = 'floor1.glb'):
        """交一层（multipart：lot、floor、idea、owner_words、file）。→ {ok, code: measuring, iid, floor, floors, text}（量好、拍好照才报价）。"""
        form = [('lot', str(lot), None, None), ('floor', str(floor or 1), None, None), ('idea', str(idea or ''), None, None),
                ('owner_words', str(owner_words or ''), None, None), ('file', bytes(glb), str(name), 'model/gltf-binary')]
        return await self.request('POST', 'isle/interior', form=form, timeout=120)

    async def isle_interior_confirm(self, body: dict):
        """主人说了「就这样」：{iid, owner_words} → {ok, code: placed | checking, paid_coin, text}。"""
        return await self.request('POST', 'isle/interior/confirm', body=body, timeout=60)

    async def isle_interiors(self):
        """插件交过的室内（最近 6 件）：{ok, interiors: [{iid, state, coin, upkeep_day_coin, lines, area_m2, rooms, problems, lot, lot_name, floors, at}]}。"""
        return await self.request('GET', 'isle/interiors', timeout=8)

    async def isle_build_confirm(self, body: dict):
        """主人说了「盖吧」：{build_id, owner_words} → {ok, paid_coin, upkeep_day_coin, …}。"""
        return await self.request('POST', 'isle/build/confirm', body=body, timeout=20)

    async def isle_builds(self):
        """插件交过的楼（最近 6 件）。"""
        return await self.request('GET', 'isle/builds', timeout=8)

    @staticmethod
    def pic_url_on(base: str, url: str) -> str | None:
        """url 是不是 base 这个地址下小镇自己的图 isle/pic/<12 位>.jpg|png（同一个协议和域名、不带查询串）；不是就 None。"""
        u = str(url or '').strip()
        root = urlsplit(base)
        if not re.match(r'^https?://', u):
            u = base + u.lstrip('/')
        p = urlsplit(u)
        if p.scheme != root.scheme or p.netloc != root.netloc or p.query or p.fragment or '..' in p.path:
            return None
        return u if p.path.startswith(root.path) and PIC_PATH.search(p.path) else None

    def isle_pic_url(self, url: str) -> str | None:
        """查到的那页带的图的地址：只认小镇自己的 isle/pic/<12 位>.jpg|png（同一个域名）；别的地址一律不取。
        0.4.5 默认地址时入口机、主站两处的都认（相对地址按现在用的那个）；入口机连不上时改到主站的同一个路径。"""
        r = self.root
        if not self.auto:
            return self.pic_url_on(r, url)
        for b in (r, API_BASE, LEGACY_BASE):
            u = self.pic_url_on(b, url)
            if u:
                return LEGACY_BASE + u[len(API_BASE):] if b == API_BASE and r == LEGACY_BASE else u
        return None

    async def isle_pic(self, url: str, timeout: float = 8) -> tuple[bytes, str]:
        """取一张图（岛-鱼看图-接口.md）：图是公开的，不带接入码；≤ PIC_BYTES 的 JPEG / PNG。→ (图, 'image/jpeg' 或 'image/png')。"""
        u = self.isle_pic_url(url)
        if not u:
            raise TownError('PIC_URL', 0, '不是小镇的图')
        http = await self._http()
        at = API_BASE if u.startswith(API_BASE) else ''
        answered = False
        try:
            async with http.get(u, headers={'User-Agent': self.ua}, timeout=aiohttp.ClientTimeout(total=timeout)) as res:
                answered = True
                self._reached(at)
                kind = (res.headers.get('Content-Type') or '').split(';')[0].strip().lower()
                if res.status != 200:
                    raise TownError(f'HTTP_{res.status}', res.status, '图没取到')
                if kind not in ('image/jpeg', 'image/png'):
                    raise TownError('PIC_TYPE', 0, kind or '?')
                data = await res.content.read(PIC_BYTES + 1)
        except aiohttp.ClientError as error:
            if not answered:
                self._unreachable(at)
            raise TownError('NETWORK', 0, redact(error)) from None
        except TimeoutError:
            if not answered:
                self._unreachable(at)
            raise TownError('NETWORK', 0, 'timeout') from None
        if not data or len(data) > PIC_BYTES:
            raise TownError('PIC_SIZE', 0, str(len(data)))
        return data, kind
