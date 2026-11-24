"""0.3.2 插件自己更新：只认我们自己的地址、版本比较、sha256 对不上不装、装交给 AstrBot 的上传安装。
python -m pytest tests/test_updater.py -q"""
import asyncio
import hashlib
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fishbowl_updater', ROOT / 'updater.py')
U = importlib.util.module_from_spec(spec)
sys.modules['fishbowl_updater'] = U
spec.loader.exec_module(U)

SHA = 'a' * 64


def test_versions():
    assert U.newer('0.3.2', '0.3.1') and U.newer('0.10.0', '0.9.9') and not U.newer('0.3.1', '0.3.1')
    assert not U.newer('0.3', 'x') and not U.newer('', '0.3.1') and not U.newer('bad', '0.3.1')


def test_only_our_own_addresses_and_a_complete_record():
    ok = {'version': '0.3.3', 'url': 'https://fishbowltown.com/dsh/pkg/astrbot/astrbot_plugin_fishbowl_town-0.3.3.zip', 'sha256': SHA.upper(),
          'notes': '说明' * 100}
    got = U.clean(ok)
    assert got['sha256'] == SHA and len(got['notes']) == 120
    gh = dict(ok, url='https://github.com/Ornn8/astrbot_plugin_fishbowl_town/releases/download/v0.3.3/astrbot_plugin_fishbowl_town-0.3.3.zip')
    assert U.clean(gh)
    for bad in (dict(ok, url='https://evil.example/x.zip'), dict(ok, url='http://fishbowltown.com/dsh/pkg/astrbot/x.zip'),
                dict(ok, url='https://fishbowltown.com/dsh/pkg/astrbot/x.tgz'), dict(ok, sha256='abc'), dict(ok, version='latest'),
                'not a dict', None):
        assert U.clean(bad) is None


class FakeRes:
    """aiohttp 的样子：read(n) 是「最多 n」，只回缓冲里那一块（0.4.13 以前就是被它坑了）；iter_chunked 一块一块给到头。"""
    def __init__(self, status, data, chunk=4096, length='same', headers=None):
        self.status, self._data = status, data
        self.content_length = len(data) if length == 'same' else length
        self.headers = headers or {}

        class C:
            async def read(_, n):
                return data[:min(n, chunk)]

            async def iter_chunked(_, n):
                for j in range(0, len(data), min(n, chunk)):
                    yield data[j:j + min(n, chunk)]
        self.content = C()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeSession:
    def __init__(self, status, data, **kw):
        self.status, self.data, self.kw = status, data, kw

    def get(self, url, **kw):
        return FakeRes(self.status, self.data, **self.kw)


def test_a_zip_is_kept_only_when_its_sha256_matches(tmp_path):
    data = b'PK fake zip'
    info = {'version': '0.3.3', 'url': 'https://fishbowltown.com/dsh/pkg/astrbot/x.zip', 'sha256': hashlib.sha256(data).hexdigest()}
    path = asyncio.run(U.download(info, tmp_path, FakeSession(200, data)))
    assert path.read_bytes() == data and path.name == 'update-0.3.3.zip'
    for session, word in ((FakeSession(200, b'changed'), '校验值'), (FakeSession(404, b''), 'HTTP 404'),
                          (FakeSession(200, b'x' * (U.ZIP_MAX + 1)), '太大')):
        try:
            asyncio.run(U.download(dict(info, version='0.3.4'), tmp_path, session))
        except RuntimeError as error:
            assert word in str(error)
        else:
            raise AssertionError('should refuse')
    assert not (tmp_path / 'update-0.3.4.zip').exists()


def test_install_goes_through_astrbots_own_upload_install(tmp_path):
    seen = []

    class Manager:
        async def install_plugin_from_file(self, path):
            seen.append(path)

    class Ctx:
        _star_manager = Manager()

    asyncio.run(U.install(Ctx(), tmp_path / 'u.zip'))
    assert seen == [str(tmp_path / 'u.zip')]

    class Old:
        _star_manager = None
    try:
        asyncio.run(U.install(Old(), tmp_path / 'u.zip'))
    except RuntimeError as error:
        assert '上传' in str(error)
    else:
        raise AssertionError('should refuse')


def test_the_note_tells_him_how():
    t = U.told_text({'version': '0.3.3', 'notes': '修了一个问题'}, '0.3.2')
    assert '/town update' in t and '0.3.3' in t and '0.3.2' in t and '修了一个问题' in t


def _info(data, version='0.4.13', url='https://fishbowltown.com/dsh/pkg/astrbot/x.zip'):
    return {'version': version, 'url': url, 'sha256': hashlib.sha256(data).hexdigest()}


def test_a_zip_sent_in_many_small_chunks_is_read_whole(tmp_path):
    """0.4.13（用户报：/town update 每次 0.2 秒内「校验值对不上」）：服务器分很多小块发，也要读到头。"""
    data = bytes(range(256)) * 1200                                              # 300 KB
    path = asyncio.run(U.download(_info(data), tmp_path, FakeSession(200, data, chunk=1000)))
    assert path.read_bytes() == data
    # 少了一截（连接断在半路，Content-Length 对不上）：不装，说没下完
    try:
        asyncio.run(U.download(_info(data, '0.4.14'), tmp_path, FakeSession(200, data[:5000], chunk=1000, length=len(data))))
    except RuntimeError as error:
        assert '没下完' in str(error) and '5000 / 307200' in str(error), error
    else:
        raise AssertionError('should refuse')
    assert not (tmp_path / 'update-0.4.14.zip').exists()
    # 压缩着传的：长度是压缩后的，不比长度，照 sha256
    path = asyncio.run(U.download(_info(data, '0.4.15'), tmp_path, FakeSession(200, data, chunk=1000, length=123, headers={'Content-Encoding': 'gzip'})))
    assert path.read_bytes() == data


def test_a_real_aiohttp_server_sending_small_flushed_chunks(tmp_path):
    """真的 aiohttp 服务器，一次 1 KB 写、每块都让出去：以前的 read(ZIP_MAX + 1) 只拿到头一块。"""
    from aiohttp import web
    from aiohttp.test_utils import TestServer

    data = bytes(range(256)) * 1200

    async def handler(request):
        res = web.StreamResponse(headers={'Content-Type': 'application/zip', 'Content-Length': str(len(data))})
        await res.prepare(request)
        for j in range(0, len(data), 1024):
            await res.write(data[j:j + 1024])
            await asyncio.sleep(0.001)
        await res.write_eof()
        return res

    async def run():
        app = web.Application()
        app.router.add_get('/x.zip', handler)
        server = TestServer(app)
        await server.start_server()
        try:
            return await U.download(_info(data, url=str(server.make_url('/x.zip'))), tmp_path)
        finally:
            await server.close()
    path = asyncio.run(run())
    assert path.read_bytes() == data


def test_a_failed_update_tells_him_the_manual_way():
    t = U.manual_text({'url': 'https://fishbowltown.com/dsh/pkg/astrbot/astrbot_plugin_fishbowl_town-0.4.13.zip'})
    assert 'astrbot_plugin_fishbowl_town-0.4.13.zip' in t and '「安装插件」→「从文件安装」→「选择文件」' in t and '设置和数据都保留' in t and '从链接安装' in t
