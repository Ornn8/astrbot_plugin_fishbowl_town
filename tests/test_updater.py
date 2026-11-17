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
    def __init__(self, status, data):
        self.status, self._data = status, data

        class C:
            async def read(_, n):
                return data[:n]
        self.content = C()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class FakeSession:
    def __init__(self, status, data):
        self.status, self.data = status, data

    def get(self, url, **kw):
        return FakeRes(self.status, self.data)


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
