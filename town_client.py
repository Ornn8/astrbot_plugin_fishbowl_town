"""小镇「外来的鱼」接口的客户端（/town/api/outside/，Authorization: Bearer fbo_…）。
接口文档：https://fishbowltown.com/town/docs/outside"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

import aiohttp

DEFAULT_BASE = 'https://fishbowltown.com/town/api/outside/'
SECRET = re.compile(r'(fbo_[A-Za-z0-9_-]{6,}|\bsk-[A-Za-z0-9_-]{8,})')
PIC_PATH = re.compile(r'/isle/pic/[0-9a-f]{12}\.(jpg|png)$')
PIC_BYTES = 1_500_000


def redact(s) -> str:
    return SECRET.sub('***', str(s or ''))


class TownError(Exception):
    def __init__(self, code: str, status: int = 0, reason: str = '', retry_after: float = 0, data=None):
        super().__init__(f'{code}: {reason}' if reason else code)
        self.code, self.status, self.reason, self.retry_after, self.data = code, status, reason, retry_after, data


def transient(code: str) -> bool:
    """小镇重启、网络抖一下时会出现的错：不停地重连，不打扰主人。"""
    return code in ('NETWORK', 'UNKNOWN', 'TOWN_OFF', 'TOWN_RESTARTING') or bool(re.fullmatch(r'HTTP_5\d\d', code))


class TownClient:
    def __init__(self, code: str, base: str = DEFAULT_BASE, user_agent: str = 'astrbot-fishbowl-town', session: aiohttp.ClientSession | None = None):
        if not isinstance(code, str) or not code.strip():
            raise TownError('NO_CODE', 0, '还没填小镇接入码')
        self.root = base if base.endswith('/') else base + '/'
        self.auth = 'Bearer ' + code.strip()
        self.ua = user_agent
        self._session = session
        self._own = session is None

    async def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._own = True
        return self._session

    async def close(self):
        if self._own and self._session is not None and not self._session.closed:
            await self._session.close()

    async def request(self, method: str, path: str, *, query: dict | None = None, body=None, timeout: float = 15):
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

    # 长轮询：有回合立刻回，没有最多等 wait 秒。kinds=activity：她在码头、电玩城、小店……说的话也交给她自己写
    async def turns(self, wait: int = 25):
        return await self.request('GET', 'turns', query={'wait': wait, 'kinds': 'activity'}, timeout=wait + 10)

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
