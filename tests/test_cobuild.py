"""0.4.7 主人和鱼一起盖楼（cobuild.py；和 DSH 插件 0.9.10 test/cobuild.test.mjs 同一套用例）：找 Blender、只跑插件自带的两个固定脚本、
主人亲手打的「交吧」「盖吧」、主人在 Blender 里改了能看出来、她再改配方时他改的那一层留着、本机先查、鲸币不够照实说。
0.4.9 一层室内也一起做（和 DSH 0.9.12 test/cobuild-interior.test.mjs 同一套）：start interior=true 取 room.json → --room 做成 floorN.glb →「交吧」交上岛 →
岛上量好才报价（插件每分钟问一次 isle/interiors）→「就这样」才确认；交楼带配方里的正门。室内跟着外面的楼走：几层就做几层，每层不比外面大。
最后一个用这台电脑上真的 Blender 跑一遍（没有就跳过）。"""
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town import cobuild as C  # noqa: E402
from astrbot_plugin_fishbowl_town import isle_tools as T  # noqa: E402
from astrbot_plugin_fishbowl_town.guide import BUILD_STEP  # noqa: E402
from astrbot_plugin_fishbowl_town.prompts import ISLE_BUILD_NOTE  # noqa: E402
from astrbot_plugin_fishbowl_town.town_client import TownClient  # noqa: E402

from test_town_base import Net, Res  # noqa: E402

MAIN = (ROOT / 'main.py').read_text(encoding='utf-8')
OWNER = 'qq:FriendMessage:42'
LOT_JSON = {'w': 12, 'd': 10, 'limits': {'tris': 150000, 'kb': 6144, 'h': 300, 'tex_n': 4, 'tex_px': 1024, 'shapes': 4000, 'shapes_json_kb': 1024}}
RECIPE = {'shapes': [{'type': 'box', 'pos': [0, 0, 0], 'size': [6, 3, 4], 'material': 'plaster', 'color': '#f2e2c4'},
                     {'type': 'gable', 'pos': [0, 3, 0], 'size': [6, 1.5, 4], 'material': 'tile'}]}
ROOM = {'foot': [-4, -3, 4, 3], 'h': 3.2, 'floor': 2, 'floors': 3, 'area_m2': 48, 'door': None, 'limits': {'tris': 144000, 'kb': 19660, 'tex_n': 5, 'tex_px': 2048}}
INSIDE = {'room': {'floor': {'material': 'wood'}, 'walls': {'material': 'plaster'}}, 'shapes': [], 'lights': [{'at': [0, 2.7, 0], 'power': 150}],
          'points': {'table': [0, 0.75, 0], 'seats': [[0, 0.45, 0.8]]}}
STYLE = ('风格', '低多边形', '低模', 'low-poly', 'lowpoly', '可爱', '温馨', '好看', '漂亮', '精致', '美观', '配色', '色调', '氛围', '童话', '卡通', '治愈', '柔和',
         '暖色', '冷色', '复古', '简约', '写实', '梦幻', 'cute', 'cozy', 'style', 'aesthetic')


def run(c):
    return asyncio.run(c)


def arg(args, k):
    a = [str(x) for x in args]
    return a[a.index(k) + 1] if k in a else ''


class FakeBlender:
    """照参数写文件、回脚本那一行 JSON 的样子。"""

    def __init__(self):
        self.calls = []

    @staticmethod
    def numbers(text):
        h = 4.5
        try:
            r = json.loads(text)
            h = max((s.get('pos') or [0, 0, 0])[1] + (s.get('size') or [0, 0, 0])[1] for s in r['shapes'])
        except (ValueError, KeyError, TypeError):
            pass
        taller = 'taller' in text
        return {'bbox': [-3, -2, 3, 2], 'h': 7.5 if taller else h, 'below': 0, 'footprint_m2': 24, 'tris': 40 if taller else 28, 'kb': 12, 'objects': 3 if taller else 2}

    @staticmethod
    def previews(d):
        if d:
            for k in C.PREVIEWS:
                Path(d, k + '.png').write_bytes(b'\x89PNG\x01\x02\x03')

    async def run(self, blender, args, **kw):
        self.calls.append(('recipe', blender, [str(a) for a in args]))
        a = [str(x) for x in args]
        if a[0] == '--recipe' and '--room' in a:            # 一层室内：尺寸照 room.json
            text = Path(a[1]).read_text('utf-8')
            Path(arg(a, '--out-blend')).write_text('BLEND ' + text, 'utf-8')
            Path(arg(a, '--out-glb')).write_text('glTF ' + text, 'utf-8')
            self.previews(arg(a, '--previews'))
            room, r = json.loads(Path(arg(a, '--room')).read_text('utf-8')), json.loads(text)
            n = {'bbox': room['foot'], 'tris': 1200, 'kb': 90, 'textures': [], 'lights': len(r.get('lights') or []), 'shapes': len(r['shapes']), 'floor': room['floor'],
                 'room': room['foot'], 'room_h': room['h'], 'points': {}}
            return {'ok': True, 'result': {'cobuild': 1, 'ok': True, 'inside': True, 'numbers': n, 'problems': [], 'notes': [], 'secs': 1}, 'why': []}
        if a[0] == '--recipe':
            text = Path(a[1]).read_text('utf-8')
            Path(arg(a, '--out-blend')).write_text('BLEND ' + text, 'utf-8')
            Path(arg(a, '--out-glb')).write_text('GLB ' + text, 'utf-8')
            self.previews(arg(a, '--previews'))
            n = {**self.numbers(text), 'shapes': len(json.loads(text)['shapes']), **({'door': json.loads(text)['door']} if 'door' in json.loads(text) else {})}
            return {'ok': True, 'result': {'cobuild': 1, 'ok': True, 'numbers': n, 'problems': [f"高 {n['h']} 米，这块地最多 300 米"] if n['h'] > 300 else [], 'notes': [], 'secs': 1.2}, 'why': []}
        if a[0] == '--from-blend':
            text = Path(a[1]).read_text('utf-8')
            if 'broken' in text:
                return {'ok': False, 'result': None, 'why': ['这个 .blend 打不开']}
            Path(arg(a, '--out-glb')).write_text('GLB ' + text, 'utf-8')
            self.previews(arg(a, '--previews'))
            return {'ok': True, 'result': {'cobuild': 1, 'ok': True, 'numbers': self.numbers(text), 'problems': [], 'notes': [], 'secs': 0.8}, 'why': []}
        return {'ok': False, 'result': None, 'why': ['不认识的参数']}

    async def layer_run(self, blender, args, **kw):
        a = [str(x) for x in args]
        self.calls.append(('layer', blender, a))
        if a[0] == 'diff':
            Path(arg(a, '--out-layer')).write_text('LAYER ' + Path(arg(a, '--owner')).read_text('utf-8'), 'utf-8')
            return {'ok': True, 'result': {'ok': True, 'stale': False, 'layer': ['Chimney', '001 box plaster'], 'added': ['Chimney'], 'changed': ['001 box plaster'],
                                           'kept': [], 'deleted': [], 'materials': []}, 'why': []}
        if a[0] == 'apply':
            Path(arg(a, '--out')).write_text(Path(arg(a, '--base')).read_text('utf-8') + '\n' + Path(arg(a, '--layer')).read_text('utf-8'), 'utf-8')
            return {'ok': True, 'result': {'ok': True, 'removed': ['001 box plaster'], 'added': ['Chimney', '001 box plaster'], 'materials': []}, 'why': []}
        return {'ok': False, 'result': None, 'why': ['不认识的参数']}


class FakeTown:
    def __init__(self, confirm=None):
        self.sent = []
        self.answers = list(confirm or [{'ok': True, 'paid_coin': 120, 'upkeep_day_coin': 2, 'text': '楼上岛了'}])
        self.inside = []                                     # isle/interiors 回的（测试里改）

    async def isle_build_guide(self, lot):
        self.sent.append(('guide', lot))
        return {'ok': True, 'lot': lot, 'lot_name': '海边小屋地', 'text': '【建模说明】形状清单……', 'limits': LOT_JSON['limits'], 'lot_json': LOT_JSON}

    async def isle_build(self, lot, idea, owner_words, glb, door=None):
        self.sent.append(('build', {'lot': lot, 'idea': idea, 'owner_words': owner_words, 'glb': glb, 'door': door}))
        return {'ok': True, 'build_id': 'b-1', 'quote': {'coin': 120, 'upkeep_day_coin': 2, 'lines': ['占地 24 平方米']}}

    async def isle_build_confirm(self, body):
        self.sent.append(('confirm', body))
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]

    async def isle_builds(self):
        return {'ok': True, 'builds': []}

    async def isle_interior_guide(self, lot, floor=None):
        self.sent.append(('inside_guide', {'lot': lot, 'floor': floor}))
        if floor == 4:
            return {'ok': False, 'code': 'bad', 'text': '室内第 4 层做不了：外面的楼只有 3 层（室内跟着外面的楼走，floor 写 1 到 3）'}
        return {'ok': True, 'lot': lot, 'lot_name': '海边小屋地', 'floor': floor or 1, 'floors': 3, 'text': '室内说明……【跟着外面的楼】……',
                'room_json': {**ROOM, 'floor': floor or 1}, 'limits': {**ROOM['limits'], 'in_mb': 64, 'floors': 3, 'area_m2': 48}}

    async def isle_interior(self, lot, floor, idea, owner_words, glb, name='floor1.glb'):
        self.sent.append(('inside', {'lot': lot, 'floor': floor, 'idea': idea, 'owner_words': owner_words, 'glb': glb, 'name': name}))
        return {'ok': True, 'code': 'measuring', 'iid': 'Iabc123456', 'floor': floor, 'floors': 3, 'text': '室内交上去了'}

    async def isle_interior_confirm(self, body):
        self.sent.append(('inside_confirm', body))
        return {'ok': True, 'code': 'checking', 'paid_coin': 128.0, 'text': '付了'}

    async def isle_interiors(self):
        self.sent.append(('inside_list', None))
        return {'ok': True, 'interiors': self.inside}


class Journal:
    def __init__(self):
        self.j = []

    def event(self, kind, text, tag='', limit=600):
        self.j.append((kind, text))


FOUND = {'ok': True, 'path': 'C:/blender/blender.exe', 'version': (5, 2, 4), 'configured': True, 'conf_missing': False, 'why': ''}


class Clock:
    def __init__(self):
        self.t = time.time() + 60

    def __call__(self):
        self.t += 0.01
        return self.t


def setup(tmp_path, found=FOUND, town=None, owner=OWNER):
    clock = Clock()
    b, told, j = FakeBlender(), [], Journal()
    town = town or FakeTown()

    async def find(_conf):
        return found

    async def tell(text, files, kind='edit'):
        told.append((text, files))
        return True

    cb = C.Cobuild(tmp_path / 'build', now=clock, journal=j, town=lambda: town, find=find, run=b.run, layer_run=b.layer_run, tell=tell,
                   is_owner_chat=lambda key: key == owner, settle_s=0.01)
    return cb, b, town, told, j


def test_vendored_scripts_are_the_islands_byte_for_byte_and_never_eval():
    assert hashlib.sha256(C.SCRIPT_FILE.read_bytes()).hexdigest() == C.SCRIPT_SHA256
    assert b'\r\n' not in C.SCRIPT_FILE.read_bytes() and b'\r\n' not in C.LAYER_FILE.read_bytes()
    server = ROOT.parent / 'wt-1124-door' / 'ops' / 'revenue-20261022' / 'fishbowl-town' / 'tools' / 'cobuild' / 'recipe_to_blend.py'
    if server.exists():
        assert hashlib.sha256(server.read_bytes()).hexdigest() == C.SCRIPT_SHA256
    dsh = ROOT.parent / 'wt-dsh-cobuild' / 'lib' / 'cobuild' / 'owner_layer.py'
    if dsh.exists():
        assert dsh.read_bytes() == C.LAYER_FILE.read_bytes(), 'same owner_layer.py as the DSH plugin'
    layer = C.LAYER_FILE.read_text('utf-8')
    assert 'use_scripts=False' in layer and not re.search(r'\beval\(|\bexec\(', layer)


def test_blender_versions_places_and_the_plain_need_blender_line():
    assert C.parse_blender_version('Blender 5.2.4\n\tbuild date') == (5, 2, 4)
    assert C.parse_blender_version('Blender 4.2') == (4, 2, 0) and C.parse_blender_version('x') is None
    assert C.version_ok((4, 2, 0)) and C.version_ok((5, 0, 1)) and not C.version_ok((4, 1, 9)) and not C.version_ok((3, 6, 5))
    win = C.blender_candidates('win32', {'ProgramFiles': 'C:\\Program Files', 'BLENDER': 'D:\\B\\blender.exe', 'PATH': 'C:\\x'}, 'C:\\Users\\u',
                               lambda d: ['Blender 4.2', 'Blender 5.2', 'readme.txt'] if d.endswith('Blender Foundation') else [])
    assert win[0] == 'D:\\B\\blender.exe'
    assert win.index('C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe') < win.index('C:\\Program Files\\Blender Foundation\\Blender 4.2\\blender.exe')
    assert 'C:\\x\\blender.exe' in win
    assert '/Applications/Blender.app/Contents/MacOS/Blender' in C.blender_candidates('darwin', {}, '/Users/u', lambda d: ['Blender.app'] if d == '/Applications' else [])
    assert '/usr/bin/blender' in C.blender_candidates('linux', {}, '/home/u', lambda d: [])
    versions = {'/a/blender': (4, 1, 0), '/b/blender': (5, 2, 4), '/c/blender': (4, 2, 3)}

    async def version(p):
        return versions.get(p)

    kw = dict(platform='linux', home='', listdir=lambda d: [], exists=lambda p: p in versions, version=version)
    best = run(C.find_blender('', env={'PATH': '/a:/c:/b'}, **kw))
    assert best['ok'] and best['path'] == '/b/blender'
    conf = run(C.find_blender('/c/blender', env={'PATH': '/a:/c:/b'}, **kw))
    assert conf['path'] == '/c/blender' and conf['configured']
    old = run(C.find_blender('', env={'PATH': '/a'}, **kw))
    assert not old['ok'] and old['why'] == 'old'
    assert C.NEED_BLENDER in C.blender_text(old) and C.BLENDER_URL in C.blender_text(old) and '4.1.0' in C.blender_text(old)
    none = run(C.find_blender('/nowhere/blender', env={}, **kw))
    assert none['why'] == 'none' and none['conf_missing']
    t = C.blender_text(none, '/nowhere/blender')
    for w in (C.NEED_BLENDER, C.BLENDER_URL, '/nowhere/blender', '别的照常'):
        assert w in t, w
    assert '找到了 Blender 5.2.4' in C.blender_text(best)


def test_run_blender_background_factory_no_autoexec_time_limit_and_her_text_never_on_the_command_line():
    seen = []

    def ok(cmd, timeout):
        seen.append((cmd, timeout))
        return 0, b'Blender says hi\n{"cobuild": 1, "ok": true, "numbers": {"h": 4.5}}\n', b'', False

    r = run(C.run_blender('B', ['--recipe', 'r.json', '--out-blend', 'b.blend', '--out-glb', 'b.glb'], threads=3, spawn=ok))
    assert r['ok'] and r['result']['numbers']['h'] == 4.5
    cmd, timeout = seen[0]
    assert cmd[:9] == ['B', '-b', '-t', '3', '--factory-startup', '--disable-autoexec', '--python-exit-code', '3', '--python']
    assert cmd[9] == str(C.SCRIPT_FILE) and cmd[10:] == ['--', '--recipe', 'r.json', '--out-blend', 'b.blend', '--out-glb', 'b.glb']
    assert '--python-expr' not in cmd and timeout == C.RUN_S
    slow = run(C.run_blender('B', [], timeout=3, spawn=lambda cmd, t: (None, b'', b'', True)))
    assert not slow['ok'] and slow['killed'] and '超过 3 秒还没做完' in slow['why'][0]
    bad = run(C.run_blender('B', [], spawn=lambda cmd, t: (2, '{"cobuild": 1, "ok": false, "why": ["第 1 个形状（box）的 size 要写成 3 个数"]}\n'.encode(), b'', False)))
    assert bad['why'] == ['第 1 个形状（box）的 size 要写成 3 个数']

    def boom(cmd, t):
        raise FileNotFoundError(2, 'No such file')

    assert 'Blender 没跑起来' in run(C.run_blender('B', [], spawn=boom))['why'][0]
    assert C.parse_result('{"owner_layer": 1, "ok": true}\n', '{"owner_layer":')['ok']
    if os.name == 'nt':
        assert subprocess.BELOW_NORMAL_PRIORITY_CLASS     # spawn_blender 用的低优先级


def test_is_yes_counts_jiao_ba_gai_ba_hao_and_not_questions_or_buts():
    for t in ('交吧', '盖吧', '好', '好的', '可以', '可以，盖吧！', '行，交了吧', 'ok', '就这样吧', '没问题，交', '交上去吧', '确认', '嗯', '价格可以'):
        assert C.is_yes(t), t
    for t in ('好看', '交吗？', '要不要交', '先别交', '不交', '等等', '再改改屋顶', '可以，但是窗户大一点', '不行', '这个可以吗', '我们盖个房子吧', '太贵了', '', '   ', 'no', 'wait'):
        assert not C.is_yes(t), t


def test_lines_numbers_changes_and_empty_lots():
    assert C.numbers_line({'bbox': [-3, -2, 3, 2], 'h': 4.5, 'footprint_m2': 24, 'tris': 28, 'kb': 12}) == '占地 6 × 4 米（24 平方米），高 4.5 米，28 个面，模型 12 KB'
    assert C.changes_line({'bbox': [-3, -2, 3, 2], 'h': 4.5, 'tris': 28, 'objects': 2}, {'bbox': [-3, -2, 3, 2.5], 'h': 6.5, 'tris': 40, 'objects': 3}) == \
        '占地从 6 × 4 米变成 6 × 4.5 米；高从 4.5 米变成 6.5 米；面数从 28 变成 40；物体从 2 个变成 3 个'
    assert '都没变' in C.changes_line({'h': 4.5, 'tris': 28}, {'h': 4.5, 'tris': 28})
    packet = {'isle': {'mine': [{'lot': 'L-0107', 'built': False}, {'lot': 'L-0108', 'built': True}], 'text': '你承包的 L-0300（海边）还没盖楼。L-0400 有楼了。'}}
    assert sorted(C.empty_lots_in(packet)) == ['L-0107', 'L-0300']


def test_no_blender_says_so_plainly_and_asks_the_town_nothing(tmp_path):
    town = FakeTown()
    cb, *_ = setup(tmp_path, found={'ok': False, 'path': '', 'version': None, 'configured': False, 'conf_missing': False, 'why': 'none'}, town=town)
    r = run(cb.start('L-0107', '小木屋'))
    assert C.NEED_BLENDER in r['text'] and C.BLENDER_URL in r['text'] and town.sent == []
    assert C.NEED_BLENDER in run(cb.status())['text']
    assert '还没开始盖' in run(cb.make(RECIPE))['text']
    assert 'lot 要填地号' in run(cb.start('../../etc'))['text']


def test_start_make_submit_only_on_his_own_jiao_ba_then_quote_then_gai_ba(tmp_path):
    town = FakeTown(confirm=[{'ok': False, 'code': 'short', 'text': '鲸币不够：要 120，有 30'}, {'ok': True, 'paid_coin': 120, 'upkeep_day_coin': 2, 'text': '楼盖好了：付了 120 鲸币，每天物业 2 鲸币'}])
    cb, b, _, _, j = setup(tmp_path, town=town)

    async def flow():
        cb.observe_owner(OWNER, '我们盖个房子吧')
        s = await cb.start('l-0107', '海边的小木屋，两层')
        assert '开始和主人在 海边小屋地（L-0107）上一起盖楼了' in s['text'] and '【建模说明】' in s['text']
        d = cb.active()
        assert json.loads(cb.lot_json(d).read_text('utf-8')) == LOT_JSON
        assert '不是一段能读的 JSON' in (await cb.make('这不是 JSON'))['text']
        assert '没有形状' in (await cb.make({'shapes': []}))['text']
        assert b.calls == []
        tall = await cb.make({'shapes': [{'type': 'box', 'pos': [0, 0, 0], 'size': [6, 400, 4]}]})
        assert '不合这块地的规矩，现在交不了' in tall['text'] and '高 400 米，这块地最多 300 米' in tall['text']
        cb.observe_owner(OWNER, '交吧')
        assert '还有不合规矩的地方' in (await cb.submit(OWNER))['text']
        m = await cb.make(json.dumps(RECIPE))
        assert '第 2 版做好了（照你的配方做的，Blender 用了 1.2 秒）' in m['text'] and '都合这块地的规矩，能交' in m['text']
        assert len(m['previews']) == 3 and m['previews'][0].endswith(os.path.join('v2', 'front.png'))
        assert '插件已经直接发到你们的私聊里了' in m['text'] and cb.work_file() in m['text'] and Path(cb.work_file()).exists()
        call = b.calls[-1][2]
        assert call[0] == '--recipe' and '--lot' in call and not any('plaster' in x for x in call)
        assert '主人看到以后还没在这里亲口说话' in (await cb.submit(OWNER))['text']
        cb.observe_owner(OWNER, '好看')
        assert '听着不像让你交' in (await cb.submit(OWNER))['text']
        cb.observe_owner('qq:GroupMessage:7', '交吧')
        assert '这个会话不算' in (await cb.submit('qq:GroupMessage:7'))['text']
        cb.observe_owner(OWNER, '交吧')
        r = await cb.submit(OWNER)
        assert '交上去了（主人的原话「交吧」一起存在楼上），岛上量好了：120 鲸币，每天物业 2 鲸币' in r['text']
        sent = next(x[1] for x in town.sent if x[0] == 'build')
        assert sent['lot'] == 'L-0107' and sent['owner_words'] == '交吧' and sent['idea'] == '海边的小木屋，两层' and isinstance(sent['glb'], bytes)
        assert '还没在这里亲口说话' in (await cb.confirm(OWNER))['text']
        cb.observe_owner(OWNER, '盖吧')
        r = await cb.confirm(OWNER)
        assert '鲸币不够，没确认' in r['text'] and '不要劝他换鲸币或者充值' in r['text'] and not re.search('¥|元|爱发电', r['text'])
        assert not cb.data['drafts'][d['id']].get('placed')
        cb.observe_owner(OWNER, '够了，盖吧')
        r = await cb.confirm(OWNER)
        assert '楼盖好了：付了 120 鲸币，之后每天物业 2 鲸币' in r['text'] and r['text'].count('楼盖好了') == 1, r['text']
        assert [x[1]['owner_words'] for x in town.sent if x[0] == 'confirm'] == ['盖吧', '够了，盖吧']
        assert cb.active() is None and cb.data['drafts'][d['id']]['placed']
        assert [(x['what'], x['owner_words']) for x in cb.data['drafts'][d['id']]['requests']] == [('submit', '交吧'), ('confirm', '够了，盖吧')]
        assert any('盖好了' in t for _, t in j.j)

    run(flow())


def test_he_edits_the_blend_she_is_told_and_her_next_recipe_keeps_his_layer(tmp_path):
    cb, b, _, told, _ = setup(tmp_path)

    async def flow():
        await cb.start('L-0200', '小塔')
        await cb.make(RECIPE)
        work = Path(cb.work_file())
        await cb.tick()
        assert told == []
        work.write_text('BLEND owner made it taller', 'utf-8')
        old = time.time() - 10
        os.utime(work, (old, old))
        await cb.tick()
        assert len(told) == 1
        text, files = told[0]
        assert '主人刚在 Blender 里亲手改了楼，插件出了第 2 版' in text and '高从 4.5 米变成 7.5 米' in text
        assert '他加的：Chimney；他改过的：001 box plaster' in text and len(files) == 3
        assert cb.take_notes() == [text] and cb.take_notes() == []
        diff = next(c for c in b.calls if c[0] == 'layer' and c[2][0] == 'diff')
        assert '--base' in diff[2] and '--owner' in diff[2] and '--keep' in diff[2]
        v = cb.current(cb.active())
        assert v['by'] == 'owner' and v['layer']['added'] == ['Chimney']
        await cb.tick()
        assert len(told) == 1
        m = await cb.make({'shapes': [{'type': 'box', 'pos': [0, 0, 0], 'size': [8, 3, 5], 'material': 'wood'}]})
        assert '照你的配方做的，叠上了主人亲手改的那一层' in m['text'] and '主人亲手改的部分留着，叠在你的新配方上：他加的：Chimney' in m['text']
        assert '你配方里同名的 001 box plaster 用的是他那一份' in m['text']
        apply = next(c for c in b.calls if c[0] == 'layer' and c[2][0] == 'apply')
        assert arg(apply[2], '--layer').endswith(os.path.join('v2', 'layer.blend'))
        assert 'owner made it taller' in work.read_text('utf-8')
        o = await cb.make(RECIPE, over_owner=True)
        assert '照主人的意思，这一版没叠他亲手改的那一层' in o['text'] and 'do="use"、version=2' in o['text']
        assert 'owner made it taller' not in work.read_text('utf-8')
        u = await cb.use(2)
        assert '换回第 2 版了（主人亲手改的）' in u['text'] and 'owner made it taller' in work.read_text('utf-8')
        assert '没有第 99 版' in (await cb.use(99))['text']
        work.write_text('BLEND broken', 'utf-8')
        os.utime(work, (old - 5, old - 5))
        await cb.tick()
        assert '主人改过的 .blend 插件读不出来：这个 .blend 打不开' in told[-1][0]

    run(flow())


def test_a_save_right_before_jiao_ba_is_shown_first(tmp_path):
    cb, _, town, _, _ = setup(tmp_path)

    async def flow():
        await cb.start('L-0300')
        await cb.make(RECIPE)
        cb.observe_owner(OWNER, '交吧')
        work = Path(cb.work_file())
        work.write_text('BLEND owner made it taller, then said submit', 'utf-8')
        old = time.time() - 10
        os.utime(work, (old, old))
        r = await cb.submit(OWNER)
        assert '主人刚在 Blender 里亲手改了楼' in r['text'] and '先给他看看这一版，他说交再交' in r['text'] and len(r['previews']) == 3
        assert not [x for x in town.sent if x[0] == 'build']
        cb.observe_owner(OWNER, '交吧')
        assert '交上去了' in (await cb.submit(OWNER))['text']

    run(flow())


def test_events_placed_by_build_id_or_by_lot_and_empty_lots_offered_once(tmp_path):
    cb, *_ = setup(tmp_path)

    async def flow():
        cb.see_turn({'packet': {'isle': {'mine': [{'lot': 'L-0500', 'built': False}]}}})
        first = cb.context_line()
        assert 'L-0500' in first and '只提这一次，他不接就算了' in first and '充值' not in first
        assert cb.context_line() == ''
        await cb.start('L-0500')
        await cb.make(RECIPE)
        assert '第 1 版能交' in cb.context_line()
        cb.observe_owner(OWNER, '交吧')
        await cb.submit(OWNER)
        assert '报价 120 鲸币，等主人点头' in cb.context_line()
        cb.on_event({'kind': 'isle_build', 'data': {'what': 'quoted', 'lot': 'L-0500', 'coin': 120}})
        assert cb.active()
        cb.on_event({'kind': 'isle_build', 'data': {'what': 'placed', 'lot': 'L-0999', 'coin': 120}})
        assert cb.active()
        cb.on_event({'kind': 'isle_build', 'data': {'what': 'placed', 'build_id': 'b-other', 'lot': 'L-0500', 'coin': 120}})
        assert cb.active()                                   # same lot, another build (11-23 events name the build)
        cb.on_event({'kind': 'isle_build', 'data': {'what': 'placed', 'lot': 'L-0500', 'coin': 120, 'by': 'owner_page'}})
        assert cb.active() is None
        assert next(iter(cb.data['drafts'].values()))['placed']['by'] == 'owner_page'

    run(flow())
    m, *_ = setup(tmp_path / 'bid')

    async def by_id():                                       # an event with build_id is matched by it, whatever lot it says
        await m.start('L-0501')
        await m.make(RECIPE)
        m.observe_owner(OWNER, '交吧')
        assert '交上去了' in (await m.submit(OWNER))['text']
        m.on_event({'kind': 'isle_build', 'data': {'what': 'placed', 'build_id': 'b-1', 'lot': 'L-9999', 'coin': 120, 'by': 'plugin'}})
        assert m.active() is None

    run(by_id())
    n, *_ = setup(tmp_path / 'intro')
    assert '新添了「一起盖楼」' in n.context_line() and n.context_line() == ''


# 0.4.9（小镇 11-24 面多、文件大的楼另收、每天物业也多）：报价的「怎么算的」一项一项都给她，面数、文件、每天物业在后面，不能截在前 4 行
def test_one_plot_several_buildings_start_new_is_its_own_draft_handed_in_with_new_and_at_without_new_a_rebuild(tmp_path):
    """一块地几栋（小镇 11-25；和 DSH test/cobuild.test.mjs 同一个用例）：start new=true（可带 x/z/ry）是另一份草稿，交楼带 new / at；
    不写 new 交上去是重建（报价写明）；室内不管 new；x/z/ry 只留是数的；小镇客户端只在 new 时带这两栏。"""
    town = FakeTown()
    quotes = [{'ok': True, 'build_id': 'b-new', 'new': True, 'quote': {'coin': 80, 'upkeep_day_coin': 2, 'new': True, 'rebuild': False, 'lines': ['在这块地上再盖一栋：按新楼算']}},
              {'ok': True, 'build_id': 'b-re', 'new': False,
               'quote': {'coin': 90, 'upkeep_day_coin': 2, 'new': False, 'rebuild': True, 'lines': ['这块地上已经有楼：起步按重建费 62.5 鲸币算', '这是重建 L-0107：换掉原来那栋，不是再盖一栋']}}]

    async def isle_build(lot, idea, owner_words, glb, door=None, addnew=False, at=None):
        town.sent.append(('build', {'lot': lot, 'addnew': addnew, 'at': at}))
        return quotes.pop(0)
    town.isle_build = isle_build
    cb, _, _, _, _ = setup(tmp_path, town=town)

    async def flow():
        cb.observe_owner(OWNER, '地够大，我们再盖一栋小棚子吧')
        s = await cb.start('L-0107', '小棚子', False, None, addnew=True, at=C.at_of(7.5, '-6', 90))
        assert '上一起再盖一栋了（交上去岛上再查这块地还能不能盖、放不放得下；放在 {"x": 7.5, "z": -6.0, "ry": 90}）' in s['text'], s['text']
        nd = cb.active()
        assert nd['addnew'] is True and nd['at'] == {'x': 7.5, 'z': -6.0, 'ry': 90}
        await cb.make(RECIPE)
        assert '（再盖一栋）' in (await cb.status())['text']
        cb.observe_owner(OWNER, '交吧')
        sub = await cb.submit(OWNER)
        assert '80 鲸币，每天物业 2 鲸币（在这块地上再盖一栋）' in sub['text'], sub['text']
        assert town.sent[-1] == ('build', {'lot': 'L-0107', 'addnew': True, 'at': {'x': 7.5, 'z': -6.0, 'ry': 90}})
        cb.observe_owner(OWNER, '第一栋也重盖一下')
        re_ = await cb.start('L-0107', '重盖第一栋')
        assert '上一起盖楼了' in re_['text'] and cb.active()['id'] != nd['id'] and 'addnew' not in cb.active()
        await cb.make(RECIPE)
        cb.observe_owner(OWNER, '交吧')
        r2 = await cb.submit(OWNER)
        assert '（是重建：换掉原来那栋）' in r2['text'] and '这是重建 L-0107：换掉原来那栋，不是再盖一栋' in r2['text'], r2['text']
        assert town.sent[-1] == ('build', {'lot': 'L-0107', 'addnew': False, 'at': None})
    run(flow())
    assert C.at_of('a', None, float('inf')) is None and C.at_of(0, '2.5') == {'x': 0, 'z': 2.5} and C.at_of(True) is None
    m = re.search(r"async def tool_build\(self, event: AstrMessageEvent([^)]*)\):\n        \"\"\"([\s\S]*?)\"\"\"\n([\s\S]*?)(?=\n    @|\n    # ---)", MAIN)
    params, doc, body = m.groups()
    assert 'new: bool = False' in params and 'new(boolean)' in doc and 'x(number)' in doc and 'ry(number)' in doc
    assert 'new=true' in doc and '不写 new 交上去是重建原来那栋' in doc
    assert 'more = new is True and interior is not True' in body and 'addnew=True, at=at_of(x, z, ry)' in body

    net = Net(lambda m_, u: Res(200, '{"ok": true, "build_id": "b-1"}'))
    c = TownClient('fbo_test_code_123456', 'https://t.test/town/api/outside/', session=net)

    def fields():
        return {f[0]['name']: f[2] for f in net.seen[-1]['data']._fields}
    run(c.isle_build('L-0107', '', '交吧', b'glTF', addnew=True, at={'x': 7.5, 'z': -6}))
    assert fields()['new'] == 'true' and json.loads(fields()['at']) == {'x': 7.5, 'z': -6}
    run(c.isle_build('L-0107', '', '交吧', b'glTF', at={'x': 1}))
    assert 'new' not in fields() and 'at' not in fields()


def test_the_quote_lines_all_reach_her_faces_file_and_daily_upkeep_come_last(tmp_path):
    lines = ['起步 62.5 鲸币（12 米、1000 立方米、20000 面、512 KB 以内）', '高度 14 米（起步含 12）：多出来的 2 米 25.0 鲸币', '体积 1200 立方米（起步含 1000）：多出来的 200 立方米 50.0 鲸币',
             '面数 80000 面（起步含 20000）：多出来的 60000 面 1500.0 鲸币', '文件 3072 KB（起步含 512）：多出来的 2560 KB 2250.0 鲸币',
             '每天物业 72.5 鲸币（起步 2.5；文件超出起步的每 MB 每天 20、面数超出起步的每一万面每天 10）']
    town = FakeTown()

    async def isle_build(lot, idea, owner_words, glb, door=None):
        town.sent.append(('build', {'lot': lot}))
        return {'ok': True, 'build_id': 'b-9', 'quote': {'coin': 3887.5, 'upkeep_day_coin': 72.5, 'lines': lines}}
    town.isle_build = isle_build
    cb, *_ = setup(tmp_path, town=town)

    async def flow():
        cb.observe_owner(OWNER, '我们盖个房子吧')
        await cb.start('L-0107', '高一点的楼')
        await cb.make(RECIPE)
        cb.observe_owner(OWNER, '交吧')
        return (await cb.submit(OWNER))['text']

    out = run(flow())
    assert '3887.5 鲸币，每天物业 72.5 鲸币' in out
    for x in lines:
        assert x in out, x


def test_wording_plain_whale_coins_no_push(tmp_path):
    cb, *_ = setup(tmp_path, town=FakeTown(confirm=[{'ok': False, 'code': 'short', 'text': '鲸币不够'}]))

    async def flow():
        texts = [T.BUILD_HERE, BUILD_STEP, ISLE_BUILD_NOTE, cb.context_line(), (await cb.start('L-0107'))['text'].split('【建模说明】')[0], (await cb.make(RECIPE))['text']]
        cb.observe_owner(OWNER, '交吧')
        texts.append((await cb.submit(OWNER))['text'])
        cb.observe_owner(OWNER, '盖吧')
        texts += [(await cb.confirm(OWNER))['text'], cb.context_line(), (await cb.status())['text']]
        return texts

    for t in run(flow()):
        for w in STYLE:
            assert w not in t.lower(), (w, t[:80])
        assert not re.search('¥|元|充值吧|去充值|爱发电', t)
    assert '鲸币' in BUILD_STEP


def test_the_tool_is_owner_private_chat_only_and_takes_his_words_not_hers():
    m = re.search(r"@filter\.llm_tool\(name='fishbowl_town_build'\)\n    async def tool_build\(self, event: AstrMessageEvent([^)]*)\):\n        \"\"\"([\s\S]*?)\"\"\"\n([\s\S]*?)(?=\n    @|\n    # ---)", MAIN)
    assert m
    params, doc, body = m.groups()
    assert 'owner_words' not in params, 'she cannot hand in his words herself'
    assert 'if not self._is_owner_chat(event) or not event.is_private_chat() or not event.is_admin():\n            return NOT_OWNER' in body
    assert 'cb.submit(umo)' in body and 'cb.confirm(umo)' in body and 'self._show(umo' in body
    for w in ('交吧', '盖吧', 'Blender', '鲸币', '不劝他花钱', 'over_owner'):
        assert w in doc, w
    assert '¥' not in doc and '充值' not in doc
    assert 'self.cobuild.observe_owner(umo, event.message_str)' in MAIN       # 只记主人（is_admin）私聊里亲手打的字
    assert "'fishbowl_town_build'" in MAIN and 'fishbowl_town_build' in T.BUILD_HERE


def test_client_hands_in_the_glb_as_multipart_with_his_words():
    import aiohttp
    net = Net(lambda m, u: Res(200, '{"ok": true, "build_id": "b-1", "quote": {"coin": 120}}'))
    c = TownClient('fbo_test_code_123456', 'https://t.test/town/api/outside/', session=net)
    assert run(c.isle_build('L-0107', '小木屋', '交吧', b'glTF....'))['build_id'] == 'b-1'
    s = net.seen[-1]
    assert s['method'] == 'POST' and s['url'] == 'https://t.test/town/api/outside/isle/build' and s['timeout'].total == 120
    form = s['data']
    assert isinstance(form, aiohttp.FormData)
    fields = {f[0]['name']: (f[0].get('filename'), f[1].get('Content-Type') if isinstance(f[1], dict) else None, f[2]) for f in form._fields}
    assert fields['lot'][2] == 'L-0107' and fields['idea'][2] == '小木屋' and fields['owner_words'][2] == '交吧'
    assert fields['file'][0] == 'house.glb' and fields['file'][2] == b'glTF....'
    run(c.isle_build_confirm({'build_id': 'b-1', 'owner_words': '盖吧'}))
    assert net.seen[-1]['url'].endswith('isle/build/confirm') and json.loads(net.seen[-1]['data']) == {'build_id': 'b-1', 'owner_words': '盖吧'}
    run(c.isle_build_guide('L-0107'))
    assert net.seen[-1]['method'] == 'GET' and net.seen[-1]['params'] == {'lot': 'L-0107'}


def test_a_floor_of_the_inside_start_room_make_floor_glb_jiao_ba_quote_by_polling_then_jiu_zhe_yang(tmp_path):
    cb, b, town, told, _ = setup(tmp_path)

    async def flow():
        s = await cb.start('L-0185', '一个柜台、两张桌子', True, 2)
        assert town.sent[0] == ('inside_guide', {'lot': 'L-0185', 'floor': 2})
        assert '开始和主人做 海边小屋地（L-0185）的 2 楼室内了' in s['text'], s['text']
        for w in (C.INSIDE_LINE, '这栋楼外面 3 层', '这一层 48 平方米', 'floor2.glb', '【室内说明】'):
            assert w in s['text'], w
        d = cb.active()
        assert d['kind'] == 'interior' and json.loads(cb.room_json(d).read_text('utf-8'))['foot'] == ROOM['foot']
        assert Path(cb.work_file(d)).name == 'floor2.blend'
        assert 'shapes 要写成清单' in (await cb.make({'shapes': 'sofa'}))['text']
        m = await cb.make(INSIDE)                                   # 家具可以一个都没有
        call = b.calls[-1][2]
        assert arg(call, '--room').endswith('room.json') and arg(call, '--out-glb').endswith('floor2.glb') and '--lot' not in call, call
        assert '这一层 8 × 6 米、层高 3.2 米' in m['text'] and '都在这一层的上限里，能交' in m['text'], m['text']
        assert '还没在这里亲口说话' in (await cb.submit(OWNER))['text']
        cb.observe_owner(OWNER, '交吧')
        sub = await cb.submit(OWNER)
        sent = next(x[1] for x in town.sent if x[0] == 'inside')
        assert sent['floor'] == 2 and sent['owner_words'] == '交吧' and sent['name'] == 'floor2.glb' and sent['glb'][:4] == b'glTF'
        assert '量好了才报价，一般几分钟' in sub['text']
        town.inside = [{'iid': 'Iabc123456', 'state': 'measuring'}]
        assert '岛上还在量这一层' in (await cb.confirm(OWNER))['text']
        assert '交上去了，岛上在量' in cb.context_line()
        asks = len([x for x in town.sent if x[0] == 'inside_list'])
        await cb.tick()
        assert len([x for x in town.sent if x[0] == 'inside_list']) == asks          # 一分钟问一次
        town.inside = [{'iid': 'Iabc123456', 'state': 'quoted', 'coin': 128, 'upkeep_day_coin': 2.4, 'area_m2': 48, 'rooms': 2,
                        'lines': ['起步 50 鲸币', '面积 48 平方米（1 层）：每平方米 1 鲸币，48 鲸币；含 4.8 万面、4.8 MB']}]
        cb.now.t += C.POLL_S + 1
        await cb.tick()
        assert len(told) == 1, told
        assert '岛上量好了 海边小屋地的 2 楼室内：128 鲸币，每天物业 2.4 鲸币（48 平方米，合 2 间客房）。怎么算的：起步 50 鲸币；面积 48 平方米' in told[0][0], told[0][0]
        assert '「就这样」' in told[0][0] and '报价 128 鲸币' in cb.context_line()
        assert '还没在这里亲口说话' in (await cb.confirm(OWNER))['text']           # 「交吧」是报价以前说的
        cb.observe_owner(OWNER, '不行，太贵了')
        assert '听着不像让你确认' in (await cb.confirm(OWNER))['text']
        cb.observe_owner(OWNER, '就这样')
        c = await cb.confirm(OWNER)
        assert ('inside_confirm', {'iid': 'Iabc123456', 'owner_words': '就这样'}) in town.sent
        assert '确认了：付了 128 鲸币。这一层的照片在过看图检查' in c['text'] and cb.active() is None
        for t in (s['text'], m['text'], sub['text'], c['text'], told[0][0]):
            assert not re.search('¥|元|饭票|充值', t), t

    run(flow())


# 室内的报价也给她最多 8 行「怎么算的」（和楼一样；面数、文件、每天物业在后面，不截在前 4 行）
def test_the_inside_quote_lines_all_reach_her_too_same_8_line_cut_as_the_house(tmp_path):
    cb, b, town, told, _ = setup(tmp_path)
    lines = ['起步 50 鲸币', '面积 120 平方米（1 层）：每平方米 1 鲸币，120 鲸币；含 12 万面、12 MB', '面数 20 万面（含 12 万）：多出来的 8 万面 800 鲸币',
             '文件 30 MB（含 12 MB）：含量 1–2 倍的 12 MB 1200 鲸币', '文件 30 MB：含量 2–4 倍的 6 MB 2400 鲸币', '每天物业 6 鲸币（每平方米 0.05）', '每天物业另加多出来的文件 18 MB × 5 = 90 鲸币']

    async def flow():
        await cb.start('L-0185', '', True, 1)
        await cb.make(INSIDE)
        cb.observe_owner(OWNER, '交吧')
        await cb.submit(OWNER)
        town.inside = [{'iid': 'Iabc123456', 'state': 'quoted', 'coin': 4570, 'upkeep_day_coin': 96, 'area_m2': 120, 'rooms': 6, 'lines': lines}]
        cb.now.t += C.POLL_S + 1
        await cb.tick()
        assert len(told) == 1, told
        for x in lines:
            assert x in told[0][0], x
        assert '报价 4570 鲸币' in cb.context_line()

    run(flow())


def test_a_refused_floor_says_why_a_floor_the_outside_lacks_names_both_numbers_the_owner_page_confirm_is_seen(tmp_path):
    cb, _, town, told, _ = setup(tmp_path)

    async def flow():
        assert '外面的楼只有 3 层' in (await cb.start('L-0185', '', True, 4))['text']
        assert 'floor 写第几层' in (await cb.start('L-0185', '', True, -1))['text']
        await cb.start('L-0185', '', True)
        assert town.sent[-1] == ('inside_guide', {'lot': 'L-0185', 'floor': None})     # 不写：岛上定（按天租的铺位是她那一层）
        await cb.make(INSIDE)
        cb.observe_owner(OWNER, '可以，交吧')
        await cb.submit(OWNER)
        town.inside = [{'iid': 'Iabc123456', 'state': 'refused', 'problems': ['1 楼室内 10.0×8.0 米，比外面这一层 8.6×6.0 米大']}]
        cb.now.t += C.POLL_S + 1
        await cb.tick()
        assert '岛上没收 海边小屋地的 1 楼室内（什么都没扣）：1 楼室内 10.0×8.0 米，比外面这一层 8.6×6.0 米大' in told[-1][0], told
        assert '上次交的室内岛上没收' in (await cb.status())['text']
        cb.observe_owner(OWNER, '交吧')
        await cb.submit(OWNER)
        town.inside = [{'iid': 'Iabc123456', 'state': 'checking', 'coin': 99, 'upkeep_day_coin': 2, 'lot': 'L-0185', 'lot_name': '海边小屋地', 'floors': [1], 'rooms': 2}]
        st = await cb.status()                                     # 主人在账户页点了确认
        assert cb.active() is None
        assert '交过的室内：海边小屋地 1 楼（照片在检查，99 鲸币，合 2 间客房）' in st['text'], st['text']

    run(flow())


def test_the_house_hand_in_carries_the_recipe_door_and_old_events_never_close_an_inside(tmp_path):
    cb, _, town, _, _ = setup(tmp_path / 'door')

    async def door():
        await cb.start('L-0107')
        await cb.make({'shapes': [{'type': 'box', 'pos': [0, 0, 0], 'size': [6, 3, 4]}], 'door': {'side': 'left', 'at': 0.5}})
        cb.observe_owner(OWNER, '交吧')
        await cb.submit(OWNER)
        assert next(x[1] for x in town.sent if x[0] == 'build')['door'] == {'side': 'left', 'at': 0.5}

    run(door())
    cb2, *_ = setup(tmp_path / 'event')

    async def event():
        await cb2.start('L-0185', '', True)
        await cb2.make(INSIDE)
        cb2.observe_owner(OWNER, '交吧')
        await cb2.submit(OWNER)
        cb2.on_event({'kind': 'isle_build', 'data': {'what': 'placed', 'lot': 'L-0185'}})          # 老的楼事件（没有号）：不是这一层
        assert cb2.active()
        cb2.on_event({'kind': 'isle_build', 'data': {'what': 'inside_placed', 'iid': 'Iabc123456', 'coin': 50}})
        assert cb2.active() is None

    run(event())


def test_client_sends_door_and_floor_as_form_fields_tool_takes_interior_and_floor_readme_says_the_inside_rule():
    net = Net(lambda m, u: Res(200, '{"ok": true, "iid": "I1", "code": "measuring", "build_id": "b-1"}'))
    c = TownClient('fbo_test_code_123456', 'https://t.test/town/api/outside/', session=net)

    def fields():
        return {f[0]['name']: (f[0].get('filename'), f[2]) for f in net.seen[-1]['data']._fields}

    run(c.isle_build('L-1', '', '交吧', b'glTF', door={'side': 'back', 'at': -1}))
    assert json.loads(fields()['door'][1]) == {'side': 'back', 'at': -1}
    run(c.isle_build('L-1', '', '交吧', b'glTF'))
    assert 'door' not in fields()
    run(c.isle_interior('L-1', 2, '', '交吧', b'glTF', name='floor2.glb'))
    assert net.seen[-1]['url'].endswith('isle/interior') and fields()['floor'][1] == '2' and fields()['file'] == ('floor2.glb', b'glTF')
    run(c.isle_interior_guide('L-1', 3))
    assert net.seen[-1]['url'].endswith('isle/interior_guide') and net.seen[-1]['params'] == {'lot': 'L-1', 'floor': '3'}
    run(c.isle_interior_guide('L-1'))
    assert net.seen[-1]['params'] == {'lot': 'L-1'}
    run(c.isle_interiors())
    assert net.seen[-1]['url'].endswith('isle/interiors')
    run(c.isle_interior_confirm({'iid': 'I1', 'owner_words': '就这样'}))
    assert net.seen[-1]['url'].endswith('isle/interior/confirm') and json.loads(net.seen[-1]['data']) == {'iid': 'I1', 'owner_words': '就这样'}
    m = re.search(r"async def tool_build\(self, event: AstrMessageEvent([^)]*)\):\n        \"\"\"([\s\S]*?)\"\"\"\n([\s\S]*?)(?=\n    @|\n    # ---)", MAIN)
    params, doc, body = m.groups()
    assert 'interior: bool = False' in params and 'floor: int = 0' in params
    assert 'cb.start(lot, idea, interior is True, floor or None)' in body
    assert C.INSIDE_LINE in doc and 'interior(boolean)' in doc and 'floor(number)' in doc and '就这样' in doc
    assert "kind == 'inside'" in MAIN and '【鱼缸小镇插件 · 室内】' in MAIN and 'interior=true' in T.BUILD_HERE
    readme = (ROOT / 'README.md').read_text('utf-8')
    k = readme.index('楼的面数、文件大小、高度按这块地的上限')
    assert C.INSIDE_LINE in readme[k:k + 200]
    vendored = (ROOT / 'cobuild_scripts' / 'README.md').read_text('utf-8')
    assert C.INSIDE_LINE in vendored and 'area_m2' in vendored


REAL = next((p for p in (os.environ.get('BLENDER'), r'F:\Tools\blender-5.2.4-windows-x64\blender.exe', r'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe') if p and os.path.isfile(p)), None)


@pytest.mark.skipif(not REAL, reason='这台电脑上没有 Blender')
def test_real_blender_recipe_previews_his_chimney_kept_on_her_new_recipe(tmp_path):
    town, told = FakeTown(), []

    async def tell(text, files):
        told.append(text)
        return True

    cb = C.Cobuild(tmp_path / 'build', blender_path=lambda: REAL, town=lambda: town, tell=tell, is_owner_chat=lambda key: True)

    async def flow():
        assert '一起盖楼了' in (await cb.start('L-0107', '小木屋'))['text']
        m = await cb.make(RECIPE)
        assert '第 1 版做好了' in m['text'] and '能交' in m['text'], m['text']
        assert len(m['previews']) == 3 and all(os.path.getsize(f) > 1000 for f in m['previews'])
        assert abs(cb.current(cb.active())['numbers']['h'] - 4.5) < 0.05
        edit = subprocess.run([REAL, '-b', '--factory-startup', '--disable-autoexec', '--python', str(ROOT / 'tests' / 'cobuild_owner_edit.py'), '--', cb.work_file()],
                              capture_output=True, timeout=120)
        assert b'"owner_edit": 1' in edit.stdout
        old = time.time() - 10
        os.utime(cb.work_file(), (old, old))
        await cb.tick()
        assert len(told) == 1 and '他加的：Chimney' in told[0] and '高从 4.5 米变成 6.5 米' in told[0], told
        m3 = await cb.make({'shapes': [{'type': 'box', 'pos': [0, 0, 0], 'size': [8, 3, 5], 'material': 'wood'}, {'type': 'gable', 'pos': [0, 3, 0], 'size': [8, 1.5, 5], 'material': 'tile'}]})
        assert '主人亲手改的部分留着' in m3['text'], m3['text']
        v3 = cb.current(cb.active())
        assert v3['numbers']['h'] > 6.4 and v3['numbers']['bbox'][2] - v3['numbers']['bbox'][0] > 7.9 and not v3['problems']
        time.sleep(0.05)   # Windows 上 time.time() 一格约 16 毫秒：他的「交吧」要晚于这一版给他看的那一刻（不然偶尔算成同一刻）
        cb.observe_owner('x', '交吧')
        assert '交上去了' in (await cb.submit('x'))['text']
        assert next(x[1] for x in town.sent if x[0] == 'build')['glb'][:4] == b'glTF'

    run(flow())
