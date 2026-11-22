"""0.4.5：外来的鱼的入口机 api.fishbowltown.com；默认地址时连不上就退回主站 fishbowltown.com 10 分钟（和 DSH 插件 0.9.8 test/town-base.test.mjs 同一套用例）。"""
import asyncio
import json
import sys
from pathlib import Path

import aiohttp
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town.town_client import (API_BASE, DEFAULT_BASE, FALLBACK_S, LEGACY_BASE, TownClient,  # noqa: E402
                                                      TownError, is_default_base)


class Res:
    def __init__(self, status=200, body='{"ok": true}', headers=None, content=b''):
        self.status, self._body, self.headers = status, body, headers or {}
        self._content = content
        self.content = self

    async def text(self):
        return self._body

    async def read(self, _n=-1):
        return self._content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class Net:
    """假的 aiohttp 会话：plan(method, url) 决定这一次是回话（Res）还是连不上（抛错，在收到回应之前）。"""

    def __init__(self, plan):
        self.plan, self.seen, self.closed = plan, [], False

    def request(self, method, url, params=None, headers=None, data=None, timeout=None):
        self.seen.append({'method': method, 'url': url, 'params': params or {}, 'data': data, 'timeout': timeout})
        net = self

        class Ctx:
            async def __aenter__(self):
                r = net.plan(method, url)
                if isinstance(r, BaseException):
                    raise r
                return r

            async def __aexit__(self, *a):
                return False
        return Ctx()

    def get(self, url, headers=None, timeout=None):
        return self.request('GET', url, timeout=timeout)

    def hosts(self):
        return [s['url'].split('/')[2] for s in self.seen]


def refused():
    return aiohttp.ClientConnectionError('Cannot connect to host api.fishbowltown.com:443')


def busy():
    return Res(503, '{"error": "TOWN_RESTARTING", "retry_after": 3}', {'Retry-After': '3'})


class Log:
    def __init__(self):
        self.lines = []

    def info(self, m):
        self.lines.append(m)


def run(coro):
    return asyncio.run(coro)


def test_the_default_is_the_api_entry_and_the_old_default_counts_as_default():
    assert DEFAULT_BASE == 'https://api.fishbowltown.com/town/api/outside/'
    assert LEGACY_BASE == 'https://fishbowltown.com/town/api/outside/'
    for b in (None, '', '  ', API_BASE, LEGACY_BASE, 'https://fishbowltown.com/town/api/outside', 'https://api.fishbowltown.com/town/api/outside'):
        assert is_default_base(b), b
    for b in ('https://fish.bside-moon.cn/town/api/outside/', 'https://t.test/town/api/outside/', 'http://127.0.0.1:8080/town/api/outside/'):
        assert not is_default_base(b), b
    schema = json.loads((ROOT / '_conf_schema.json').read_text(encoding='utf-8'))
    assert schema['base']['default'] == API_BASE


def test_two_network_failures_in_a_row_switch_to_the_old_base_for_ten_minutes_then_back():
    clock = [1000.0]
    api_down = [True]
    log = Log()
    net = Net(lambda m, u: refused() if '//api.fishbowltown.com/' in u and api_down[0] else Res(200, '{"turns": []}'))
    c = TownClient('fbo_test_code_123456', session=net, log=log, now=lambda: clock[0])

    async def go():
        assert c.root == API_BASE
        with pytest.raises(TownError) as e:
            await c.status()
        assert e.value.code == 'NETWORK'
        assert c.root == API_BASE, 'one failure is not enough'
        with pytest.raises(TownError):
            await c.events(0)
        assert c.root == LEGACY_BASE
        assert len(log.lines) == 1 and 'api.fishbowltown.com 连着 2 次连不上，先改走主站 fishbowltown.com，10 分钟后再试' in log.lines[0]
        # the long poll and the answer keep their own shape on the old base
        await c.turns(25)
        await c.answer('ot_1', '{"action":"none"}')
        poll, post = net.seen[-2:]
        assert poll['url'] == LEGACY_BASE + 'turns' and poll['params'] == {'wait': '25', 'kinds': 'activity,build'}
        assert poll['timeout'].total == 35
        assert post['method'] == 'POST' and post['url'] == LEGACY_BASE + 'turns/ot_1'
        assert json.loads(post['data']) == {'answer': '{"action":"none"}'}
        clock[0] += FALLBACK_S - 1
        await c.status()
        assert net.seen[-1]['url'].startswith(LEGACY_BASE), 'still inside the 10 minutes'
        clock[0] += 2
        api_down[0] = False
        await c.status()
        assert net.seen[-1]['url'] == API_BASE + 'status'
        assert len(log.lines) == 2 and '改回小镇的接口入口 api.fishbowltown.com' in log.lines[1]
    run(go())


def test_not_in_a_row_and_timeouts():
    seq = [refused(), Res(), refused()]
    net = Net(lambda m, u: seq.pop(0) if seq else Res())
    c = TownClient('fbo_test_code_123456', session=net)

    async def go():
        for _ in range(3):
            try:
                await c.status()
            except TownError:
                pass
        assert c.root == API_BASE, 'a failure, a reply, a failure: no switch'
        t = Net(lambda m, u: TimeoutError())
        d = TownClient('fbo_test_code_123456', session=t)
        for _ in range(2):
            with pytest.raises(TownError) as e:
                await d.turns(25)
            assert e.value.code == 'NETWORK'
        assert d.root == LEGACY_BASE, 'a timeout before any reply counts'
    run(go())


def test_http_errors_never_switch():
    codes = [busy(), busy(), busy(), Res(500, ''), Res(404, '{"error": "NO_SUCH"}')]
    net = Net(lambda m, u: codes.pop(0) if codes else busy())
    c = TownClient('fbo_test_code_123456', session=net)

    async def go():
        seen = []
        for _ in range(5):
            with pytest.raises(TownError) as e:
                await c.status()
            seen.append((e.value.code, e.value.status))
        assert seen[0] == ('TOWN_RESTARTING', 503) and seen[3] == ('HTTP_500', 500) and seen[4] == ('NO_SUCH', 404)
        assert c.root == API_BASE
        assert set(net.hosts()) == {'api.fishbowltown.com'}
        # a reply in between resets the count: failure, 503, failure -> no switch
        seq = [refused(), busy(), refused()]
        m = Net(lambda *_: seq.pop(0) if seq else Res())
        d = TownClient('fbo_test_code_123456', session=m)
        for _ in range(3):
            with pytest.raises(TownError):
                await d.status()
        assert d.root == API_BASE
    run(go())


def test_a_base_of_her_own_never_switches():
    async def go():
        for base in ('https://t.test/town/api/outside/', 'https://fish.bside-moon.cn/town/api/outside/'):
            log = Log()
            net = Net(lambda *_: refused())
            c = TownClient('fbo_test_code_123456', base, session=net, log=log)
            for _ in range(5):
                with pytest.raises(TownError):
                    await c.status()
            assert c.root == base and set(net.hosts()) == {base.split('/')[2]} and log.lines == []
    run(go())


def test_the_old_default_saved_in_the_config_is_the_default_too():
    clock = [1000.0]
    net = Net(lambda m, u: refused() if '//api.fishbowltown.com/' in u else Res())
    c = TownClient('fbo_test_code_123456', 'https://fishbowltown.com/town/api/outside/', session=net, now=lambda: clock[0])

    async def go():
        for _ in range(2):
            with pytest.raises(TownError):
                await c.status()
        await c.status()
        assert net.hosts() == ['api.fishbowltown.com', 'api.fishbowltown.com', 'fishbowltown.com']
        clock[0] += FALLBACK_S
        with pytest.raises(TownError):
            await c.status()
        assert net.hosts()[-1] == 'api.fishbowltown.com', 'after 10 minutes the api entry is tried again'
    run(go())


def test_pictures_on_both_town_bases_and_from_the_old_base_while_the_api_entry_is_down():
    clock = [1000.0]

    def plan(m, u):
        if '//api.fishbowltown.com/' in u:
            return refused()
        if '/isle/pic/' in u:
            r = Res(200, '', {'Content-Type': 'image/jpeg'})
            r._content = b'\xff\xd8abc'
            return r
        return Res()
    net = Net(plan)
    c = TownClient('fbo_test_code_123456', session=net, now=lambda: clock[0])
    on_api = API_BASE + 'isle/pic/0123456789ab.jpg'
    on_main = LEGACY_BASE + 'isle/pic/0123456789ab.jpg'
    assert c.isle_pic_url(on_api) == on_api
    assert c.isle_pic_url(on_main) == on_main, 'the town may still hand out main-site picture links'
    assert c.isle_pic_url('isle/pic/0123456789ab.png') == API_BASE + 'isle/pic/0123456789ab.png'
    for bad in ('https://evil.example/town/api/outside/isle/pic/0123456789ab.jpg', API_BASE + 'isle/pic/zz.jpg', on_api + '?x=1',
                'http://api.fishbowltown.com/town/api/outside/isle/pic/0123456789ab.jpg'):
        assert c.isle_pic_url(bad) is None, bad

    async def go():
        with pytest.raises(TownError):
            await c.isle_pic(on_api)
        with pytest.raises(TownError):
            await c.status()
        assert c.root == LEGACY_BASE, 'a picture that could not be reached counts as the api entry failing'
        assert c.isle_pic_url(on_api) == on_main
        data, kind = await c.isle_pic(on_api)
        assert kind == 'image/jpeg' and data == b'\xff\xd8abc' and net.seen[-1]['url'] == on_main
    run(go())


def test_real_sockets_a_refused_api_entry_falls_back_to_a_real_server(monkeypatch):
    """Real aiohttp exceptions: a closed port on the 'api entry' (connection refused) twice -> the 'old base' that answers."""
    import socket
    from aiohttp import web
    from astrbot_plugin_fishbowl_town import town_client as tc

    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    closed_port = s.getsockname()[1]
    s.close()

    async def go():
        async def status(_request):
            return web.json_response({'fish_id': 'abc'})
        app = web.Application()
        app.router.add_get('/town/api/outside/status', status)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        monkeypatch.setattr(tc, 'API_BASE', f'http://127.0.0.1:{closed_port}/town/api/outside/')
        monkeypatch.setattr(tc, 'LEGACY_BASE', f'http://127.0.0.1:{port}/town/api/outside/')
        c = tc.TownClient('fbo_test_code_123456', tc.API_BASE)
        try:
            for _ in range(2):
                with pytest.raises(TownError) as e:
                    await c.status()
                assert e.value.code == 'NETWORK'
            assert (await c.status()) == {'fish_id': 'abc'}
        finally:
            await c.close()
            await runner.cleanup()
    run(go())
