"""小镇「外来的鱼」接口的客户端（/town/api/outside/，Authorization: Bearer fbo_…）。
接口文档：https://fishbowltown.com/town/docs/outside"""
from __future__ import annotations

import json
import re
import time
from urllib.parse import urlsplit

import aiohttp

DEFAULT_BASE = 'https://fishbowltown.com/town/api/outside/'
SECRET = re.compile(r'(fbo_[A-Za-z0-9_-]{6,}|\bsk-[A-Za-z0-9_-]{8,})')
PIC_PATH = re.compile(r'/isle/pic/[0-9a-f]{12}\.(jpg|png)$')
PIC_BYTES = 1_500_000
# 长轮询时告诉小镇这个程序还答哪几种回合（小镇 outside_turns.py advertise()）
TURN_KINDS = 'activity,build'


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
    def __init__(self, code: str, base: str = DEFAULT_BASE, user_agent: str = 'astrbot-fishbowl-town', session: aiohttp.ClientSession | None = None):
        if not isinstance(code, str) or not code.strip():
            raise TownError('NO_CODE', 0, '还没填小镇接入码')
        self.root = base if base.endswith('/') else base + '/'
        self.auth = 'Bearer ' + code.strip()
        self.ua = user_agent
        self._session = session
        self._own = session is None
        self.shut: dict = {}        # 0.4.3 老镇那一块 -> (到什么时候前不再去问, 那次的错)

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._own = True
        return self._session

    async def close(self):
        if self._own and self._session is not None and not self._session.closed:
            await self._session.close()

    async def request(self, method: str, path: str, *, query: dict | None = None, body=None, timeout: float = 15):
        if core_path(path):
            try:
                return await self._request(method, path, query=query, body=body, timeout=timeout)
            except TownError as error:          # 小镇在重启 / 连不上：重启以后各块可能又开了，记着的「关了」作废
                if error.code == 'NETWORK' or error.status >= 500:
                    self.shut.clear()
                raise
        part = path.split('/')[0]
        was = self.shut.get(part)
        if was and was[0] > time.monotonic():            # 刚说过关了：不再去问
            raise TownError(was[1].code, was[1].status, was[1].reason, 0, was[1].data)
        try:
            return await self._request(method, path, query=query, body=body, timeout=timeout)
        except TownError as error:
            if closed(error):
                self.shut[part] = (time.monotonic() + CLOSED_S, error)
            raise

    async def _request(self, method: str, path: str, *, query: dict | None = None, body=None, timeout: float = 15):
        params = {k: str(v) for k, v in (query or {}).items() if v is not None}
        headers = {'Authorization': self.auth, 'User-Agent': self.ua, **({'Content-Type': 'application/json'} if body is not None else {})}
        http = await self._http()
        try:
            async with http.request(method, self.root + path, params=params, headers=headers,
                                    data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
                                    timeout=aiohttp.ClientTimeout(total=timeout)) as res:
                text = await res.text()
                status, retry = res.status, res.headers.get('Retry-After')
        except aiohttp.ClientError as error:
            raise TownError('NETWORK', 0, redact(error)) from None
        except TimeoutError:
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

    def isle_pic_url(self, url: str) -> str | None:
        """查到的那页带的图的地址：只认小镇自己的 isle/pic/<12 位>.jpg|png（同一个域名）；别的地址一律不取。"""
        u = str(url or '').strip()
        root = urlsplit(self.root)
        if not re.match(r'^https?://', u):
            u = self.root + u.lstrip('/')
        p = urlsplit(u)
        if p.scheme != root.scheme or p.netloc != root.netloc or p.query or p.fragment or '..' in p.path:
            return None
        return u if p.path.startswith(root.path) and PIC_PATH.search(p.path) else None

    async def isle_pic(self, url: str, timeout: float = 8) -> tuple[bytes, str]:
        """取一张图（岛-鱼看图-接口.md）：图是公开的，不带接入码；≤ PIC_BYTES 的 JPEG / PNG。→ (图, 'image/jpeg' 或 'image/png')。"""
        u = self.isle_pic_url(url)
        if not u:
            raise TownError('PIC_URL', 0, '不是小镇的图')
        http = await self._http()
        try:
            async with http.get(u, headers={'User-Agent': self.ua}, timeout=aiohttp.ClientTimeout(total=timeout)) as res:
                kind = (res.headers.get('Content-Type') or '').split(';')[0].strip().lower()
                if res.status != 200:
                    raise TownError(f'HTTP_{res.status}', res.status, '图没取到')
                if kind not in ('image/jpeg', 'image/png'):
                    raise TownError('PIC_TYPE', 0, kind or '?')
                data = await res.content.read(PIC_BYTES + 1)
        except aiohttp.ClientError as error:
            raise TownError('NETWORK', 0, redact(error)) from None
        except TimeoutError:
            raise TownError('NETWORK', 0, 'timeout') from None
        if not data or len(data) > PIC_BYTES:
            raise TownError('PIC_SIZE', 0, str(len(data)))
        return data, kind
