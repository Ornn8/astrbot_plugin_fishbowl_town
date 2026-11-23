"""0.4.7 主人和鱼一起盖楼（DSH 插件 lib/cobuild.js 的 Python 版；小镇 platform/方案-主人和鱼一起盖楼.md、接口-一起盖楼.md；
主人 11-22「不管外接还是托管，盖楼都是必须主人参与的事情吧？」「我是倾向让他们在自己的设备上建模的，只提交模型」
「要求装blender，然后我说的和主人一起盖的能一起设计和建造」）。

只在主人和她的私聊里（main.py 的 fishbowl_town_build 工具；回合里不盖楼）：
  1. 找 Blender：配置里填的路径 > 环境变量 BLENDER > 常见的安装位置（Windows / macOS / Linux）> PATH，要 4.2 或更新。
     没有就照实说「盖楼要先装 Blender（4.2 或更新）」、给下载地址，别的照常。
  2. 起头（do=start）：向小镇取这块地的建模说明（GET isle/build_guide），回答里的 lot_json 原样存成 lot.json。
  3. 她写配方（形状清单，纯数据；do=make）：插件存成 recipe.json，交给插件自带的固定脚本 cobuild_scripts/recipe_to_blend.py（和岛上建造工人
     同一个文件，不改一个字），用这台电脑上的 Blender 后台跑（-b --factory-startup --disable-autoexec，限时、低优先级、一次只开一个）：
     .blend + .glb + 三张预览图 + 量出来的数 + 不合这块地规矩的地方。她的模型写的东西从不当代码跑：插件只把 JSON 数据交给这个脚本。
     三张预览图由插件直接发到主人的私聊（AstrBot 的工具结果只带字）。
  4. 主人可以用 Blender 打开 house.blend 亲手改，保存后插件看出来（时间 / 大小变了、内容的 sha256 变了），用第二个固定脚本
     cobuild_scripts/owner_layer.py 记下他改了哪些，再用 --from-blend 出新的一版，把预览图发给主人、告诉她尺寸、高度怎么变了。
     她之后再改配方：插件从配方重新做，再把主人改的那一层叠上去（owner_layer.py apply），他改的不被盖掉；他同意不要了才 over_owner。
  5. 交（do=submit）：主人在这个私聊里亲手打的字，在看到这一版以后，明确说了「交吧」这类话，插件才把 glb 交上岛
     （POST isle/build，owner_words = 他的原话，200 字以内）。岛上当场报价（鲸币）。
  6. 确认（do=confirm）：报价给了她以后，主人又亲手明确说了「盖吧」这类话，插件才确认（POST isle/build/confirm）。
只讲怎么做、怎么交，不讲楼该长什么样（主人 11-20「不要带风格化的提示词」）。岛上的钱一律叫鲸币；她不劝主人花钱。
不认识 AstrBot：小镇客户端、发给主人、告诉她，都是传进来的，测试里换成假的。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import ntpath
import os
import posixpath
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote

from .town_client import TownError

HERE = Path(__file__).resolve().parent
SCRIPT_FILE = HERE / 'cobuild_scripts' / 'recipe_to_blend.py'
LAYER_FILE = HERE / 'cobuild_scripts' / 'owner_layer.py'
# 小镇 tools/cobuild/recipe_to_blend.py（11-23 那一版）的 sha256：插件原样带着，测试查一字不差
SCRIPT_SHA256 = '3e0b835c321aeffe5ca916404e365625ab8a29bd169b8cb4e8fa37aa1647a450'
MIN_BLENDER = (4, 2)
BLENDER_URL = 'https://www.blender.org/download/'
NEED_BLENDER = '盖楼要先装 Blender（4.2 或更新）'
RUN_S = 120                    # 一次 Blender 最多跑这么久（和岛上建造工人一样 120 秒）
OWNER_WORDS_MAX = 200
IDEA_MAX = 200
PREVIEWS = ('front', 'three_quarter', 'top')
PREVIEW_ZH = {'front': '正面', 'three_quarter': '斜 45°', 'top': '俯视'}
WATCH_S = 4                    # 有正在盖的楼时，多久看一眼 house.blend 变没变
SETTLE_S = 2.5                 # 文件刚变：等它停下来（Blender 还在存）再读
FIND_KEEP_S = 600
KEEP_VERSIONS = 30
QUOTE_LINES = 8                # 报价的「怎么算的」（楼和室内都一样）：起步、高、体积、面数、文件、重建、每天物业一项一行（小镇 11-24 面多、文件大的另收），0.4.7 只给前 4 行
LOT_RE = re.compile(r'^[A-Za-z0-9_-]{1,24}$')
# 0.4.9 一层室内也和主人一起做（小镇 11-23：开店、开游戏、开场子、开工作站，楼里要有室内）：start 写 interior=True（floor 第几层）取这一层的
# room.json（GET isle/interior_guide），同一个脚本 --room 做成 floorN.glb，「交吧」交上岛（POST isle/interior），岛上量好、拍好照才报价
# （插件每分钟问一次 GET isle/interiors），「就这样」才确认（POST isle/interior/confirm）。交楼时配方里的正门（numbers.door）一起交。
INSIDE_LINE = '室内跟着外面的楼走：几层就做几层，每层不比外面大，面数和文件按面积给'
POLL_S = 60                    # 交上去的室内：多久问一次岛上量好了没有
OUT_MAX = 4 * 1024 * 1024


def clip(v, n: int) -> str:
    t = re.sub(r'\s+', ' ', str(v if v is not None else '')).strip()
    return t[:n] if len(t) > n else t


def num(v, d: int = 1):
    try:
        x = round(float(v), d)
    except (TypeError, ValueError):
        return 0
    return int(x) if x == int(x) else x


def _list(v) -> list:
    return [str(x) for x in v if str(x)] if isinstance(v, list) else []


# --------------------------------------------------------------------------------------------------------------------------------
# Blender：在哪、什么版本
# --------------------------------------------------------------------------------------------------------------------------------
def parse_blender_version(text) -> tuple | None:
    """`blender --version` 的输出 -> (主, 次, 补丁)（读不出 None）。"""
    m = re.search(r'Blender\s+(\d+)\.(\d+)(?:\.(\d+))?', str(text or ''), re.I)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None


def version_ok(v) -> bool:
    return isinstance(v, (tuple, list)) and len(v) >= 2 and tuple(v[:2]) >= MIN_BLENDER


def version_text(v) -> str:
    return '.'.join(str(x) for x in v) if isinstance(v, (tuple, list)) else '?'


def _newer_first(name: str):
    return [-int(x) for x in re.findall(r'\d+', os.path.basename(name))]


def blender_candidates(platform: str = sys.platform, env=None, home: str | None = None, listdir=os.listdir) -> list:
    """常见的安装位置 + PATH 里的，按这个顺序试（同一个系统里新的版本在前）。只列路径，不碰文件。"""
    env = os.environ if env is None else env
    home = str(Path.home()) if home is None else home
    win = platform.startswith('win')
    P = ntpath if win else posixpath
    out: list = []

    def add(p):
        if p and p not in out:
            out.append(p)

    def dirs(root, rx):
        try:
            names = [n for n in listdir(root) if re.match(rx, n, re.I)]
        except OSError:
            return []
        return [P.join(root, n) for n in sorted(names, key=_newer_first)]

    exe = 'blender.exe' if win else 'blender'
    b = str(env.get('BLENDER') or '').strip().strip('"')
    if b:
        add(b)
        add(P.join(b, exe))
    if win:
        roots = []
        for r in (env.get('ProgramFiles') or 'C:\\Program Files', env.get('ProgramW6432'), env.get('ProgramFiles(x86)'),
                  env.get('LOCALAPPDATA') and P.join(env['LOCALAPPDATA'], 'Programs')):
            if r and r not in roots:
                roots.append(r)
        for r in roots:
            for d in dirs(P.join(r, 'Blender Foundation'), r'^blender'):
                add(P.join(d, 'blender.exe'))
        for r in roots:
            add(P.join(r, 'Steam', 'steamapps', 'common', 'Blender', 'blender.exe'))
        if home:
            add(P.join(home, 'scoop', 'apps', 'blender', 'current', 'blender.exe'))
    elif platform == 'darwin':
        for r in [x for x in ('/Applications', home and P.join(home, 'Applications')) if x]:
            for d in dirs(r, r'^blender.*\.app$'):
                add(P.join(d, 'Contents', 'MacOS', 'Blender'))
        if home:
            add(P.join(home, 'Library', 'Application Support', 'Steam', 'steamapps', 'common', 'Blender', 'Blender.app', 'Contents', 'MacOS', 'Blender'))
    else:
        for p in ('/usr/bin/blender', '/usr/local/bin/blender', '/snap/bin/blender', '/var/lib/flatpak/exports/bin/org.blender.Blender'):
            add(p)
        if home:
            add(P.join(home, '.local', 'share', 'flatpak', 'exports', 'bin', 'org.blender.Blender'))
        for r in [x for x in ('/opt', home) if x]:
            for d in dirs(r, r'^blender'):
                add(P.join(d, 'blender'))
        if home:
            add(P.join(home, '.steam', 'steam', 'steamapps', 'common', 'Blender', 'blender'))
    for d in str(env.get('PATH') or env.get('Path') or '').split(';' if win else ':'):
        d = d.strip().strip('"')
        if d:
            add(P.join(d, exe))
    return out


def _configured_candidates(conf: str, platform: str) -> list:
    """配置里填的：可以是 blender 本身、它所在的文件夹、或者 macOS 的 Blender.app。"""
    win = platform.startswith('win')
    P = ntpath if win else posixpath
    c = conf.strip().strip('"').strip()
    if not c:
        return []
    if re.search(r'\.app/?$', c, re.I):
        return [P.join(c, 'Contents', 'MacOS', 'Blender')]
    return [c, P.join(c, 'blender.exe' if win else 'blender'), P.join(c, 'Blender.app', 'Contents', 'MacOS', 'Blender')]


def _no_window() -> int:
    return getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0


def _version_sync(path: str, timeout: float = 20) -> tuple | None:
    try:
        r = subprocess.run([path, '--version'], stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, creationflags=_no_window())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return parse_blender_version(r.stdout.decode('utf-8', 'replace'))


async def blender_version(path: str) -> tuple | None:
    """跑一次 `<blender> --version`（20 秒）-> (主, 次, 补丁) 或 None。"""
    return await asyncio.to_thread(_version_sync, path)


async def find_blender(configured: str = '', *, platform: str = sys.platform, env=None, home: str | None = None, listdir=os.listdir,
                       exists=os.path.isfile, version=blender_version) -> dict:
    """找 Blender -> {ok, path, version, configured, conf_missing, why: '' | 'none' | 'old'}。
    配置里填了就先试它（找不到、打不开就记一笔，再照常找）；别的位置里挑版本最新的（最多试 6 个存在的）。"""
    conf = str(configured or '').strip()
    conf_missing = False
    if conf:
        for p in _configured_candidates(conf, platform):
            if not exists(p):
                continue
            v = await version(p)
            if v and version_ok(v):
                return {'ok': True, 'path': p, 'version': v, 'configured': True, 'conf_missing': False, 'why': ''}
            if v:
                return {'ok': False, 'path': p, 'version': v, 'configured': True, 'conf_missing': False, 'why': 'old'}
        conf_missing = True
    best = old = None
    tried = 0
    for p in blender_candidates(platform, env, home, listdir):
        if tried >= 6:
            break
        if not exists(p):
            continue
        tried += 1
        v = await version(p)
        if not v:
            continue
        if version_ok(v):
            if not best or tuple(v) > tuple(best[1]):
                best = (p, v)
        elif not old or tuple(v) > tuple(old[1]):
            old = (p, v)
    if best:
        return {'ok': True, 'path': best[0], 'version': best[1], 'configured': False, 'conf_missing': conf_missing, 'why': ''}
    if old:
        return {'ok': False, 'path': old[0], 'version': old[1], 'configured': False, 'conf_missing': conf_missing, 'why': 'old'}
    return {'ok': False, 'path': '', 'version': None, 'configured': False, 'conf_missing': conf_missing, 'why': 'none'}


def blender_text(found, configured: str = '') -> str:
    """找没找到、什么版本、去哪下载：给主人（/town build）和她（工具）看的一段话。没有、太旧都直说「盖楼要先装 Blender（4.2 或更新）」。"""
    found = found or {}
    conf = f'配置里填的 Blender 路径（{clip(configured, 80)}）找不到或打不开。' if found.get('conf_missing') else ''
    if found.get('ok'):
        return f"{conf}找到了 Blender {version_text(found.get('version'))}：{found.get('path')}{'（配置里填的）' if found.get('configured') else ''}。能一起盖楼。"
    where = '装好再试；装在别处的话，到插件配置里填「Blender 路径」（blender 程序的完整路径）。没有 Blender 只是盖不了楼，别的照常。'
    if found.get('why') == 'old':
        return f"{conf}这台电脑上的 Blender 是 {version_text(found.get('version'))}（{found.get('path')}），太旧了。{NEED_BLENDER}：{BLENDER_URL}（免费，选 LTS 版就行）。{where}"
    return f'{conf}这台电脑上没找到 Blender。{NEED_BLENDER}：{BLENDER_URL}（免费）。{where}'


# --------------------------------------------------------------------------------------------------------------------------------
# 跑插件自带的两个固定脚本
# --------------------------------------------------------------------------------------------------------------------------------
def parse_result(text, head: str = '{"cobuild":'):
    """脚本最后打的那行 JSON（以 {"cobuild": 或 {"owner_layer": 开头）；读不出 None。"""
    lines = [ln.strip() for ln in str(text or '').splitlines() if ln.strip().startswith(head)]
    for ln in reversed(lines):
        try:
            o = json.loads(ln)
        except ValueError:
            continue
        if isinstance(o, dict):
            return o
    return None


def _decode(b: bytes) -> str:
    s = (b or b'').decode('utf-8', 'replace')
    if '\ufffd' not in s:
        return s
    try:
        return b.decode('gbk')
    except UnicodeDecodeError:
        return s


def blender_threads() -> int:
    """Blender 用几个线程：一半的核（至少 1 个），别把主人的电脑占满。"""
    return max(1, (os.cpu_count() or 2) // 2)


_ONE = threading.Lock()        # Blender 一次只开一个（她做一版、主人改了出图，都排队）


def spawn_blender(cmd: list, timeout: float) -> tuple:
    """真的开 Blender：低优先级（Windows「低于正常」，别的系统 nice 10），到点杀掉。-> (退出码, stdout, stderr, 杀掉了没有)。"""
    flags = (getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0) | _no_window()) if os.name == 'nt' else 0
    with _ONE:
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)
        if os.name != 'nt':
            try:
                os.setpriority(os.PRIO_PROCESS, p.pid, 10)
            except (OSError, AttributeError):
                pass
        try:
            out, err = p.communicate(timeout=timeout)
            return p.returncode, out[:OUT_MAX], err[-8192:], False
        except subprocess.TimeoutExpired:
            p.kill()
            out, err = p.communicate()
            return p.returncode, out[:OUT_MAX], err[-8192:], True


def blender_argv(script, args, threads: int | None = None) -> list:
    """blender -b -t N --factory-startup --disable-autoexec --python-exit-code 3 --python <脚本> -- <args>。args 只是文件路径和固定的开关。"""
    return ['-b', '-t', str(threads or blender_threads()), '--factory-startup', '--disable-autoexec', '--python-exit-code', '3',
            '--python', str(script), '--', *[str(a) for a in args]]


async def run_blender(blender: str, args: list, *, script=SCRIPT_FILE, head: str = '{"cobuild":', json_out: str = '', timeout: float = RUN_S,
                      threads: int | None = None, spawn=spawn_blender) -> dict:
    """用 Blender 后台跑插件自带的一个固定脚本，从不带她写的东西。-> {ok, result（脚本的 JSON）, why: [..], code, killed}。"""
    cmd = [str(blender), *blender_argv(script, args, threads)]
    try:
        code, out, err, killed = await asyncio.to_thread(spawn, cmd, timeout)
    except (OSError, ValueError) as error:
        return {'ok': False, 'result': None, 'why': [f'Blender 没跑起来：{clip(getattr(error, "strerror", None) or error, 80)}'], 'code': None, 'killed': False}
    result = None
    if json_out:
        try:
            result = json.loads(Path(json_out).read_text('utf-8-sig'))
        except (OSError, ValueError):
            result = None
    if not isinstance(result, dict):
        result = parse_result(_decode(out), head)
    if isinstance(result, dict) and result.get('ok') is True and not killed:
        return {'ok': True, 'result': result, 'why': [], 'code': code, 'killed': False}
    if killed:
        why = [f'Blender 超过 {round(timeout)} 秒还没做完，停掉了（楼太复杂，或者这台电脑太忙）']
    elif isinstance(result, dict) and isinstance(result.get('why'), list) and result['why']:
        why = [clip(w, 200) for w in result['why']][:5]
    else:
        tail = ' '.join([ln for ln in _decode(err).splitlines() if ln.strip()][-2:])
        why = [f'Blender 没给出结果（退出码 {code}）：{clip(tail, 160) or "没说原因"}']
    return {'ok': False, 'result': result, 'why': why, 'code': code, 'killed': killed}


async def run_script(blender: str, args: list, **kw) -> dict:
    """配方脚本（recipe_to_blend.py）。"""
    return await run_blender(blender, args, script=kw.pop('script', SCRIPT_FILE), head='{"cobuild":', **kw)


async def run_layer(blender: str, args: list, **kw) -> dict:
    """主人那一层的脚本（owner_layer.py）。"""
    return await run_blender(blender, args, script=kw.pop('script', LAYER_FILE), head='{"owner_layer":', **kw)


# --------------------------------------------------------------------------------------------------------------------------------
# 主人的话：只认他自己在这个私聊里打的字
# --------------------------------------------------------------------------------------------------------------------------------
NO_RE = re.compile(r"不(行|要|交|盖|用|好|对|同意|满意|急|是|太|够)|别(交|盖|急|动)|先别|先不|算了|等(等|一下|会|下)|再(等|改|想|看|加|弄|调)|还没|没好|改(一下|改|成|个)|"
                   r"换(成|个|一)|但是|不过|可是|太贵|贵了|\bno\b|nope|\bwait\b|don'?t|\bstop\b|\bnot\b", re.I)
YES_RE = re.compile(r'交吧|交了吧|交上去|交给岛|提交|可以交|去交|就交|交啦|盖吧|盖了吧|盖起来|开盖|就盖|可以盖|去盖|上岛|确认|同意|没问题|就这(样|个|么)|就它|定了|'
                    r'就按这个|买吧|付吧|扣吧|价(钱|格)?(可以|行|没问题|ok)', re.I)
SHORT_RE = re.compile(r'^(好|好的|好啊|好吧|好嘞|好呀|行|行吧|行啊|可以|可以了|可以的|嗯|嗯嗯|嗯呢|ok|okay|yes|yep|sure|go|冲|交|盖|中|妥|对)$', re.I)
ASK_RE = re.compile(r'[?？]\s*$|吗|要不要|行不行|可不可以|好不好')


def _bare(s: str) -> str:
    return ''.join(ch for ch in s if not ch.isspace() and unicodedata.category(ch)[0] not in 'PS')


def is_yes(text) -> bool:
    """主人这句话是不是明确的「交 / 盖 / 同意」：有「不、别、等等、再改、但是」的不算，问句不算；「好看」不算，「好」「交吧」「可以，盖吧」算。"""
    raw = str(text if text is not None else '').lower().strip()
    t = _bare(raw)
    if not t or NO_RE.search(raw) or ASK_RE.search(raw):
        return False
    return bool(SHORT_RE.match(t) or YES_RE.search(t))


# 0.4.12（11-23 一位 AstrBot 主人叫鱼交二楼，鱼说「插件看到的最后一行是 QQ 私聊里的系统时间戳」，「交吧」认不出来）：别的插件、平台给消息
# 加上的时间、系统那种行不是他打的话。整行只有日期、时刻、星期、「当前时间 / 系统」这类字的，认他的话时拿掉；拿掉以后什么都不剩的那一条不记。
_TIME_BITS = re.compile(
    r'\d{2,4}\s*[-/年.]\s*\d{1,2}\s*[-/月.]\s*\d{1,2}\s*日?'
    r'|\d{1,2}\s*[:：]\s*\d{2}(?:\s*[:：]\s*\d{2}(?:\.\d+)?)?'
    r'|(?:星期|周|礼拜)[一二三四五六日天]|上午|下午|中午|凌晨|早上|晚上|傍晚|半夜'
    r'|当前时间|现在时间|现在是|系统时间|北京时间|东京时间|发送时间|消息时间|收到时间|时间戳|时间|日期|系统消息|系统提示|系统'
    r'|\b(?:am|pm|utc|gmt|cst|jst|timestamp|time|date|now|current|system)\b|[+-]\d{2}:?\d{2}\b|(?<=\d)[tTzZ]\b|(?<=\d)[tT](?=\d)', re.I)
_META_TAG = re.compile(r'<\s*(system[\w-]*|time[\w-]*|current[_-]?time|timestamp|date)\b[^>]*>.*?<\s*/\s*\1\s*>', re.I | re.S)
_BRACKET = re.compile(r'[\[【(（<〔]([^\[\]【】()（）<>〔〕]{0,80})[\]】)）>〕]')


def _meta_only(s: str) -> bool:
    return not any(ch.isalnum() for ch in _TIME_BITS.sub('', str(s)))


def owner_text(text) -> str:
    """主人这一条里他自己打的字：拿掉时间、系统那种行（和只装着时间的括号），别的原样。"""
    s = _META_TAG.sub('\n', str(text if text is not None else ''))
    s = _BRACKET.sub(lambda m: '' if _meta_only(m.group(1)) else m.group(0), s)
    return '\n'.join(ln.strip() for ln in s.splitlines() if ln.strip() and not _meta_only(ln)).strip()


class OwnerWords:
    """每个私聊里主人最近亲手打的几句（只在内存里：插件重载后要他再说一声）。observe 只交主人亲手打的字进来（main.py 认 is_admin + 私聊）。"""

    def __init__(self, now=time.time, keep: int = 6):
        self.now, self.keep = now, keep
        self.by: dict = {}

    def observe(self, key, text, at: float | None = None):
        k, t = str(key or ''), owner_text(text)
        if not k or not t:
            return
        lst = self.by.setdefault(k, [])
        lst.append({'text': t[:2000], 'at': self.now() if at is None else at})
        del lst[:-self.keep]

    def latest(self, key):
        lst = self.by.get(str(key or ''))
        return lst[-1] if lst else None


# --------------------------------------------------------------------------------------------------------------------------------
# 尺寸
# --------------------------------------------------------------------------------------------------------------------------------
def _size(n):
    b = n.get('bbox') if isinstance(n, dict) else None
    try:
        b = [float(x) for x in b] if isinstance(b, list) and len(b) == 4 else None
    except (TypeError, ValueError):
        b = None
    return (b[2] - b[0], b[3] - b[1]) if b else None


def numbers_line(n) -> str:
    """一版的尺寸：占地多宽多深、多高、多少面、多大。"""
    if not isinstance(n, dict):
        return '尺寸没量出来'
    room = n.get('room')
    if isinstance(room, list) and len(room) == 4:         # 一层室内（--room）
        try:
            r = [float(x) for x in room]
        except (TypeError, ValueError):
            r = None
        if r:
            parts = [f"这一层 {num(r[2] - r[0])} × {num(r[3] - r[1])} 米、层高 {num(n.get('room_h'))} 米", f"{round(float(n.get('tris') or 0))} 个面",
                     f"模型 {round(float(n.get('kb') or 0))} KB", f"{len(n['textures'])} 张贴图" if isinstance(n.get('textures'), list) else '',
                     f"{n.get('lights')} 盏灯" if isinstance(n.get('lights'), (int, float)) else '', f"{n.get('shapes')} 件家具" if isinstance(n.get('shapes'), (int, float)) else '']
            return '，'.join(p for p in parts if p)
    wd = _size(n)
    try:
        below = float(n.get('below') or 0)
    except (TypeError, ValueError):
        below = 0
    parts = [f"占地 {num(wd[0])} × {num(wd[1])} 米（{num(n.get('footprint_m2'))} 平方米）" if wd else '',
             f"高 {num(n.get('h'))} 米" + (f'，石台下面还有 {num(below)} 米' if below > 0 else ''),
             f"{round(float(n.get('tris') or 0))} 个面", f"模型 {round(float(n.get('kb') or 0))} KB"]
    return '，'.join(p for p in parts if p)


def changes_line(a, b) -> str:
    """两版比，变了的几样（主人亲手改了以后告诉她）。"""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return ''
    out = []
    wa, wb = _size(a), _size(b)
    if wa and wb and (abs(wa[0] - wb[0]) >= 0.05 or abs(wa[1] - wb[1]) >= 0.05):
        out.append(f'占地从 {num(wa[0])} × {num(wa[1])} 米变成 {num(wb[0])} × {num(wb[1])} 米')
    fa, fb = float(a.get('h') or 0), float(b.get('h') or 0)
    if abs(fa - fb) >= 0.05:
        out.append(f'高从 {num(fa)} 米变成 {num(fb)} 米')
    ta, tb = round(float(a.get('tris') or 0)), round(float(b.get('tris') or 0))
    if ta != tb:
        out.append(f'面数从 {ta} 变成 {tb}')
    if b.get('objects') is not None and a.get('objects') != b.get('objects'):
        out.append(f"物体从 {a.get('objects') or 0} 个变成 {b.get('objects')} 个")
    return '；'.join(out) if out else '尺寸、高度、面数都没变（改的是颜色、材料或者细节）'


def _names(v, n: int = 6) -> str:
    x = _list(v)
    return f"{'、'.join(x[:n])} 等 {len(x)} 个" if len(x) > n else '、'.join(x)


def layer_line(spec) -> str:
    """主人那一层：他加的、改过的、删掉的、改过的材料（物体名照 Blender 里的写）。"""
    if not isinstance(spec, dict):
        return ''
    parts = [f"他加的：{_names(spec.get('added'))}" if _list(spec.get('added')) else '', f"他改过的：{_names(spec.get('changed'))}" if _list(spec.get('changed')) else '',
             f"他删掉的：{_names(spec.get('deleted'))}" if _list(spec.get('deleted')) else '', f"他改过的材料：{_names(spec.get('materials'))}" if _list(spec.get('materials')) else '']
    return '；'.join(p for p in parts if p)


def has_layer(spec) -> bool:
    return isinstance(spec, dict) and bool(_list(spec.get('layer')) or _list(spec.get('deleted')) or _list(spec.get('materials')))


def empty_lots_in(packet) -> list:
    """回合里看到的「还没盖楼」的地号：{lot, built: false} 或「还没盖楼」那一句前面的地号（回合的样子会变，整个包翻一遍）。"""
    out: list = []
    seen: set = set()

    def walk(v, depth):
        if depth > 12 or v is None:
            return
        if isinstance(v, str):
            if '还没盖楼' in v:
                for m in re.finditer(r'(L-\d{2,6})(?:(?!L-\d)[\s\S]){0,240}?还没盖楼', v):
                    if m.group(1) not in out:
                        out.append(m.group(1))
            return
        if not isinstance(v, (dict, list)) or id(v) in seen:
            return
        seen.add(id(v))
        if isinstance(v, dict):
            lot = v.get('lot')
            if isinstance(lot, str) and re.fullmatch(r'L-\d{2,6}', lot) and (v.get('built') is False or v.get('在做') == '还没盖楼') and lot not in out:
                out.append(lot)
        for x in (v if isinstance(v, list) else v.values()):
            walk(x, depth + 1)

    walk(packet, 0)
    return out


def file_uri(p) -> str:
    return 'file:///' + quote(str(p).replace('\\', '/').lstrip('/'), safe='/:')


OFF = '插件现在没在小镇值班（还没填小镇接入码，或者插件配置里的开关关着），这会儿盖不了楼。'
ASK_OWNER = '主人看过、在这个私聊里明确说了「交吧」，再用 do="submit" 交给岛上（插件只认他自己在这里打的字）。'
NO_PUSH = '照实告诉主人就好，不要劝他换鲸币或者充值。'
SHOWN = '三张预览图（正面、斜 45°、俯视）插件已经直接发到你们的私聊里了，主人看得到；你看不到图，照尺寸和他说的来。'
REZH = {'quoted': '报了价，等确认', 'placed': '盖好了', 'refused': '没收', 'void': '作废了'}


class Cobuild:
    """她的盖楼：dir = 插件数据目录下的 build/。
    town() -> 小镇客户端（没值班 None）；show(text, files) 把插件的话和预览图发到主人的私聊；tell(text) 告诉她（主人改了楼）；
    is_owner_chat(key) 这个会话是不是她和主人的私聊。她的工具的几个动作都回 {text, previews}。"""

    def __init__(self, dir, *, log=None, blender_path=lambda: '', town=lambda: None, tell=None, is_owner_chat=lambda key: True, owner_words: OwnerWords | None = None,
                 journal=None, now=time.time, find=find_blender, run=run_script, layer_run=run_layer, settle_s: float = SETTLE_S):
        self.dir = Path(dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log = log
        self.blender_path, self.town, self.tell, self.is_owner_chat = blender_path, town, tell, is_owner_chat
        self.now = now
        self.owner_words = owner_words or OwnerWords(now=now)
        self.journal = journal
        self.find, self.run, self.layer_run = find, run, layer_run
        self.settle_s = settle_s
        self.index = self.dir / 'drafts.json'
        try:
            data = json.loads(self.index.read_text('utf-8'))
        except (OSError, ValueError):
            data = None
        self.data = data if isinstance(data, dict) else {}
        for k in ('drafts', 'offered', 'lotsSeen'):
            if not isinstance(self.data.get(k), dict):
                self.data[k] = {}
        self._found, self._found_at = None, 0.0
        self._lock: asyncio.Lock | None = None
        self.notes: list = []         # 下一次主人跟她说话时附给她的（主人改了楼）

    # ---- 小工具 ----
    def _say(self, level: str, msg: str):
        try:
            if self.log is not None:
                getattr(self.log, level)(f'[鱼缸小镇] {msg}')
        except Exception:  # noqa: BLE001 - 只是一行日志
            pass

    def save(self):
        try:
            tmp = self.index.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), 'utf-8')
            os.replace(tmp, self.index)
        except OSError as error:
            self._say('warning', f'盖楼的记录没存上：{error}')

    def _note(self, text: str):
        try:
            if self.journal is not None:
                self.journal.event('build', text)
        except Exception:  # noqa: BLE001 - 只是记一笔
            pass

    def _q(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def blender(self, force: bool = False) -> dict:
        if not force and self._found and self.now() - self._found_at < FIND_KEEP_S:
            return self._found
        try:
            self._found = await self.find(str(self.blender_path() or ''))
        except Exception as error:  # noqa: BLE001 - 找不到就当没有
            self._found = {'ok': False, 'why': 'none', 'path': '', 'version': None, 'conf_missing': False}
            self._say('info', f'找 Blender 出错：{error!r}')
        self._found_at = self.now()
        return self._found

    def active(self):
        a = self.data.get('active')
        return self.data['drafts'].get(a) if a else None

    def draft_dir(self, d) -> Path:
        return self.dir / d['id']

    def work_file(self, d=None) -> str:
        d = d or self.active()
        return str(self.draft_dir(d) / (f"floor{d['floor']}.blend" if self.is_in(d) else 'house.blend')) if d else ''

    @staticmethod
    def is_in(d) -> bool:
        """0.4.9：一层室内的草稿。"""
        return bool(d and d.get('kind') == 'interior')

    def glb_name(self, d) -> str:
        return f"floor{d['floor']}.glb" if self.is_in(d) else 'house.glb'      # 一层一个 .glb，按外面的楼层叫

    def room_json(self, d) -> Path:
        return self.draft_dir(d) / 'room.json'

    def where(self, d) -> str:
        if not self.is_in(d):
            return f"{d.get('lotName') or d['lot']} 上的楼"
        nm = d.get('lotName') or d['lot']
        return f"{nm}{' ' if re.search(r'[A-Za-z0-9]$', nm) else ''}的 {d['floor']} 楼室内"

    def vdir(self, d, n) -> Path:
        return self.draft_dir(d) / f'v{n}'

    @staticmethod
    def version_of(d, n):
        try:
            n = int(n)
        except (TypeError, ValueError):
            return None
        return next((v for v in (d or {}).get('versions') or [] if v.get('n') == n), None)

    def current(self, d):
        return self.version_of(d, (d or {}).get('current'))

    def lot_json(self, d) -> Path:
        return self.draft_dir(d) / 'lot.json'

    @staticmethod
    def _hash(p) -> str:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()

    @staticmethod
    def _stat(p):
        try:
            s = os.stat(p)
        except OSError:
            return None
        return {'mtime': s.st_mtime, 'size': s.st_size}

    def previews_of(self, d, v) -> list:
        return [str(self.vdir(d, v['n']) / f'{k}.png') for k in PREVIEWS if (self.vdir(d, v['n']) / f'{k}.png').exists()] if v else []

    def _next_n(self, d) -> int:
        return max([v['n'] for v in d.get('versions') or []] or [0]) + 1

    def _lot_args(self, d) -> list:
        if self.is_in(d):                                     # 室内：这一层的 room.json
            return ['--room', str(self.room_json(d))] if self.room_json(d).exists() else []
        return ['--lot', str(self.lot_json(d))] if self.lot_json(d).exists() else []

    def _layer_file(self, d, spec) -> Path:
        return self.vdir(d, spec['n']) / 'layer.blend'

    @staticmethod
    def _spec(r, n) -> dict:
        return {'n': n, **{k: _list(r.get(k)) for k in ('layer', 'added', 'changed', 'deleted', 'materials')}}

    def shown(self, d, v):
        d.setdefault('shownAt', {})[str(v['n'])] = self.now()
        self.save()

    def _sync_work(self, d, v):
        src = self.vdir(d, v['n']) / 'house.blend'
        if not src.exists():
            return
        shutil.copyfile(src, self.work_file(d))
        d['work'] = {**(self._stat(self.work_file(d)) or {}), 'hash': self._hash(self.work_file(d)), 'n': v['n']}

    @staticmethod
    def _write_json(p, obj):
        Path(p).write_text(json.dumps(obj, ensure_ascii=False), 'utf-8')

    def _version(self, res, n, by, frm) -> dict:
        return {'n': n, 'by': by, 'at': self.now(), 'from': frm, 'numbers': res.get('numbers') or {}, 'problems': res.get('problems') if isinstance(res.get('problems'), list) else [],
                'notes': res.get('notes') if isinstance(res.get('notes'), list) else [], 'secs': res.get('secs') or 0}

    def _add(self, d, v):
        d['versions'] = [*(d.get('versions') or []), v][-KEEP_VERSIONS:]   # 记录留最近 30 版（文件夹都在本机，不删）
        d['current'] = v['n']

    def version_text(self, d, v, head: str = '') -> str:
        by = '主人在 Blender 里亲手改的' if v.get('by') == 'owner' else '照你的配方做的，叠上了主人亲手改的那一层' if has_layer(v.get('layer')) else '照你的配方做的'
        probs = v.get('problems') or []
        lines = [head or f"第 {v['n']} 版做好了（{by}{'，Blender 用了 ' + str(num(v['secs'])) + ' 秒' if v.get('secs') else ''}）。",
                 f"尺寸：{numbers_line(v.get('numbers'))}。",
                 (f"本机查过了，不合这{'一层' if self.is_in(d) else '块地'}的规矩，现在交不了（告诉主人哪里不行，改好再交）：\n" + '\n'.join(f'- {clip(p, 120)}' for p in probs[:6])) if probs
                 else '本机查过了：家具都在墙里、没顶到天花板，面数、文件、贴图都在这一层的上限里，能交（岛上还会照外面那一层的大小再量一遍）。' if self.is_in(d)
                 else '本机查过了：地界、高度、面数、文件大小都合这块地的规矩，能交。',
                 ('提醒：' + '；'.join(clip(x, 120) for x in (v.get('notes') or [])[:3])) if v.get('notes') else '',
                 SHOWN if self.previews_of(d, v) else '（这一版没出预览图）',
                 f'主人想亲手改：用 Blender 打开 {self.work_file(d)}，改完保存（Ctrl+S），插件会看出来、出新的一版（Blender 里已经开着这个文件的，先 File > Revert 重新载入这一版再改）。',
                 '改好了再交。' if probs else ASK_OWNER]
        return '\n'.join(x for x in lines if x)

    def owner_changed_text(self, d, v) -> str:
        probs = v.get('problems') or []
        return '\n'.join(x for x in [
            f"主人刚在 Blender 里亲手改了{str(d['floor']) + ' 楼的室内' if self.is_in(d) else '楼'}，插件出了第 {v['n']} 版：{v.get('changes')}{'（' + v['edit'] + '）' if v.get('edit') else ''}。",
            f"他改的是 Blender 里开着的旧的一版（没重新载入），插件把他改的那几处叠到了第 {v['stale']['onto']} 版上。" if v.get('stale') else '',
            f"现在：{numbers_line(v.get('numbers'))}。" + (f"本机查过，有 {len(probs)} 处不合规矩：{'；'.join(clip(x, 60) for x in probs[:3])}。" if probs else '本机查过，合规矩，能交。'),
        ] if x)

    # ---- 主人改了 house.blend 没有 ----
    async def _sync_owner(self, d) -> dict:
        """改了就出新的一版。-> {changed, version} | {pending} | {failed, text} | {}。"""
        if not d or d.get('placed') or not d.get('work'):
            return {}
        wf = self.work_file(d)
        st = self._stat(wf)
        if not st:
            return {}
        if st['mtime'] == d['work'].get('mtime') and st['size'] == d['work'].get('size'):
            return {}
        if time.time() - st['mtime'] < self.settle_s:
            return {'pending': True}
        try:
            h = self._hash(wf)
        except OSError:
            return {'pending': True}
        if h == d['work'].get('hash'):
            d['work'].update(st)
            self.save()
            return {}
        b = await self.blender()
        prev = self.current(d)
        base_v = self.version_of(d, d['work'].get('n')) or prev
        d['work'] = {**st, 'hash': h, 'n': d['work'].get('n')}
        if not b.get('ok'):
            self.save()
            return {'failed': True, 'text': f"主人改了 {wf}，可这台电脑上 Blender 用不了，插件出不了新的一版。{blender_text(b, str(self.blender_path() or ''))}"}
        n = self._next_n(d)
        vd = self.vdir(d, n)
        vd.mkdir(parents=True, exist_ok=True)
        owner = vd / 'owner.blend'
        shutil.copyfile(wf, owner)
        spec, stale = None, False
        # 1. 他改了哪些（和他开始改的那一版比；他 Blender 里开着的还是更早的一版就和那一版比）
        if base_v and (self.vdir(d, base_v['n']) / 'house.blend').exists():
            keep = vd / 'keep.json'
            self._write_json(keep, base_v.get('layer') or {})
            older = [x for x in d.get('versions') or [] if x['n'] < base_v['n']][-1:]
            alt = older[0] if older and (self.vdir(d, older[0]['n']) / 'house.blend').exists() else None
            if alt:
                self._write_json(vd / 'alt-keep.json', alt.get('layer') or {})
            args = ['diff', '--base', self.vdir(d, base_v['n']) / 'house.blend', '--owner', owner, '--out-layer', vd / 'layer.blend', '--keep', keep,
                    *(['--alt-base', self.vdir(d, alt['n']) / 'house.blend', '--alt-keep', vd / 'alt-keep.json'] if alt else []), '--json-out', vd / 'layer.json']
            r = await self.layer_run(b['path'], args, json_out=str(vd / 'layer.json'))
            if r['ok']:
                spec, stale = self._spec(r['result'], n), r['result'].get('stale') is True
            else:
                self._say('info', '盖楼：主人那一层没记下来：' + '；'.join(r['why']))
        # 2. 这一版的楼：他存的文件本身；他改的是旧的一版（没重新载入）就把他那一层叠到现在这一版上
        house, merged = owner, None
        if stale and spec and base_v:
            self._write_json(vd / 'layer-spec.json', spec)
            m = await self.layer_run(b['path'], ['apply', '--base', self.vdir(d, base_v['n']) / 'house.blend', '--layer', vd / 'layer.blend', '--spec', vd / 'layer-spec.json', '--out', vd / 'house.blend'])
            if m['ok']:
                house, merged = vd / 'house.blend', m['result']
            else:
                stale = False
        if house == owner:
            shutil.copyfile(owner, vd / 'house.blend')
        jo = vd / 'result.json'
        r = await self.run(b['path'], ['--from-blend', vd / 'house.blend', '--out-glb', vd / self.glb_name(d), '--previews', vd, *self._lot_args(d), '--json-out', jo], json_out=str(jo))
        if not r['ok']:
            shutil.rmtree(vd, ignore_errors=True)
            d['ownerFail'] = {'at': self.now(), 'why': r['why']}
            self.save()
            return {'failed': True, 'text': f"主人改过的 .blend 插件读不出来：{'；'.join(r['why'])}。文件还在（{wf}），改好再存一次。"}
        v = self._version(r['result'], n, 'owner', base_v and base_v['n'])
        v['layer'] = (spec if has_layer(spec) else None) if spec else ((base_v or {}).get('layer') or None)   # 没记下来：先照旧那一层
        v['changes'] = changes_line((prev or {}).get('numbers'), v['numbers'])
        v['edit'] = layer_line(spec)
        if stale and merged:
            v['stale'] = {'onto': base_v['n']}
        self._add(d, v)
        d['layer'] = v['layer']
        if stale and merged:
            self._sync_work(d, v)          # 叠好的那一版放回 house.blend：他重新载入就看到
        else:
            d['work'] = {**(self._stat(wf) or {}), 'hash': h, 'n': n}
        self.save()
        self._say('info', f"盖楼：主人亲手改了 {d['lot']}，出了第 {n} 版")
        self._note(f"主人在 Blender 里亲手改了 {self.where(d)}（第 {n} 版）：{v['changes']}{'；' + v['edit'] if v['edit'] else ''}")
        return {'changed': True, 'version': v}

    async def _settled(self, d) -> dict:
        """他正在存（文件刚变）：等它停下来再看一次，免得插件换文件时和 Blender 抢。"""
        r = await self._sync_owner(d)
        for _ in range(3):
            if not r.get('pending'):
                break
            await asyncio.sleep(self.settle_s)
            r = await self._sync_owner(d)
        return r

    # ---- 她的工具的几个动作 ----
    @staticmethod
    def _out(text, previews=None) -> dict:
        return {'text': str(text or ''), 'previews': list(previews or [])}

    async def status(self) -> dict:
        b = await self.blender(True)
        lines = [blender_text(b, str(self.blender_path() or ''))]
        d = self.active()
        if self.is_in(d) and d.get('submitted') and not d['submitted'].get('quote') and not d.get('placed'):
            await self.poll_inside(d, True)
        if d:
            v = self.current(d)
            now = (f"现在是第 {v['n']} 版（{'主人亲手改的' if v.get('by') == 'owner' else '你的配方'}），{numbers_line(v.get('numbers'))}；"
                   f"{'还有 ' + str(len(v['problems'])) + ' 处不合规矩' if v.get('problems') else '能交'}") if v else '还没做出第一版'
            lines.append(f"正在和主人{'做' if self.is_in(d) else '盖'} {self.where(d)}{'（' + clip(d['idea'], 60) + '）' if d.get('idea') else ''}：{now}。")
            if has_layer(d.get('layer')):
                lines.append(f"主人亲手改的那一层（你改配方时会叠上去）：{layer_line(d['layer'])}。")
            if v:
                lines.append(f'文件：{self.work_file(d)}')
            s = d.get('submitted')
            if s and not d.get('placed') and self.is_in(d) and not s.get('quote'):
                lines.append(f"交上去的第 {s['n']} 版岛上还在量、拍照片，量好了才报价（一般几分钟）。")
            elif s and not d.get('placed') and self.is_in(d):
                lines.append(f"{self.quote_text(d)}等主人点头（他说「就这样」再 do=\"confirm\"）。")
                s['quoteShownAt'] = self.now()
                self.save()
            elif s and not d.get('placed'):
                q = s.get('quote') or {}
                lines.append(f"交上去的第 {s['n']} 版报价 {num(q.get('coin'))} 鲸币、每天物业 {num(q.get('upkeep_day_coin'))} 鲸币，等主人点头（do=\"confirm\"）。")
            if d.get('placed'):
                lines.append(f"这一层的室内已经确认了（付了 {num(d['placed'].get('paid_coin'))} 鲸币）。" if self.is_in(d) else f"这栋楼已经盖好了（付了 {num(d['placed'].get('paid_coin'))} 鲸币）。")
            if d.get('refused') and not s:
                lines.append('上次交的室内岛上没收：' + ('；'.join(clip(x, 80) for x in _list(d['refused'].get('problems'))[:3]) or '没说原因') + '。')
        else:
            lines.append('现在没有在盖的楼。主人想盖，就用 do="start"、lot 填地号开始；做楼里的室内，do="start" 再写 interior=true（floor 第几层）。')
        seen = [lot for lot in self.data['lotsSeen'] if not any(x.get('lot') == lot and x.get('placed') for x in self.data['drafts'].values())]
        if seen:
            lines.append(f"你最近在岛上看到还没盖楼的地：{'、'.join(seen[:5])}。")
        c = self.town()
        if c is not None and hasattr(c, 'isle_builds'):
            try:
                r = await c.isle_builds()
                lst = (r or {}).get('builds') if isinstance(r, dict) else None
                if isinstance(lst, list) and lst:
                    lines.append('交过的楼：' + '；'.join(f"{x.get('lot_name') or x.get('lot')}（{REZH.get(x.get('state'), x.get('state') or '?')}"
                                                           f"{'，' + str(num((x.get('quote') or {}).get('coin'))) + ' 鲸币' if (x.get('quote') or {}).get('coin') is not None else ''}）"
                                                           for x in lst[:4] if isinstance(x, dict)) + '。')
            except (TownError, OSError):
                pass
        if c is not None and hasattr(c, 'isle_interiors'):
            zh = {'measuring': '在量', 'quoted': '报了价，等确认', 'checking': '照片在检查', 'placed': '放好了', 'refused': '没收', 'replaced': '换掉了', 'removed': '清掉了'}
            try:
                r = await c.isle_interiors()
                lst = [x for x in ((r or {}).get('interiors') or []) if isinstance(x, dict) and x.get('state') != 'void'] if isinstance(r, dict) else []
                if lst:
                    lines.append('交过的室内：' + '；'.join(
                        f"{x.get('lot_name') or x.get('lot') or '?'} {'、'.join(str(k) for k in (x.get('floors') or [])) or '?'} 楼（{zh.get(x.get('state'), x.get('state') or '?')}"
                        f"{'，' + str(num(x.get('coin'))) + ' 鲸币' if x.get('coin') is not None else ''}{'，合 ' + str(x.get('rooms')) + ' 间客房' if x.get('rooms') is not None else ''}）"
                        for x in lst[:4]) + '。')
            except (TownError, OSError):
                pass
        return self._out('\n'.join(lines), self.previews_of(d, self.current(d)) if d else [])

    async def start(self, lot='', idea='', interior: bool = False, floor=None) -> dict:
        lid = str(lot or '').strip().upper()
        if not LOT_RE.match(lid):
            return self._out('lot 要填地号（比如 L-0107；承包的楼填楼号 B-0009 也行）。不知道是哪块地，就问问主人，或者用 fishbowl_isle_look 查「我的」看看你名下的地。')
        b = await self.blender(True)
        if not b.get('ok'):
            return self._out(f"{blender_text(b, str(self.blender_path() or ''))}\n先请主人装好 Blender，再一起{'做' if interior else '盖'}。")
        c = self.town()
        if c is None:
            return self._out(OFF)
        if interior is True:
            return await self._start_inside(c, lid, idea, floor)
        try:
            g = await c.isle_build_guide(lid)
        except TownError as error:
            return self._out(self._town_fail(error, '取这块地的建模说明'))
        if not isinstance(g, dict) or g.get('ok') is not True:
            g = g if isinstance(g, dict) else {}
            if g.get('code') == 'refused':
                return self._out(f"{lid} 盖不了楼：{clip(g.get('text'), 200)}")
            if g.get('code') == 'closed':
                return self._out(f"鱼缸岛这会儿没对你开：{clip(g.get('text'), 160)}")
            return self._out(f"这会儿取不到 {lid} 的建模说明：{clip(g.get('text'), 160) or '岛上没回'}。过一会儿再试。")
        d = next((x for x in self.data['drafts'].values() if not self.is_in(x) and x.get('lot') == lid and not x.get('placed') and not x.get('ended')), None)
        if d is None:
            d = {'id': f"{lid}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(self.now()))}", 'lot': lid, 'versions': [], 'current': 0, 'startedAt': self.now(), 'shownAt': {}, 'layer': None}
            self.data['drafts'][d['id']] = d
        d['lotName'] = clip(g.get('lot_name') or '', 40)
        if isinstance(idea, str) and idea.strip():
            d['idea'] = clip(idea, IDEA_MAX)
        d['limits'] = g.get('limits') if isinstance(g.get('limits'), dict) else None
        self.draft_dir(d).mkdir(parents=True, exist_ok=True)
        if isinstance(g.get('lot_json'), dict):
            self._write_json(self.lot_json(d), g['lot_json'])
        (self.draft_dir(d) / 'guide.txt').write_text(str(g.get('text') or ''), 'utf-8')
        self.data['active'] = d['id']
        self.save()
        self._note(f"你和主人开始在 {d['lotName'] or lid} 上一起盖楼")
        v = self.current(d)
        return self._out('\n'.join([
            f"开始和主人在 {d['lotName'] + '（' + lid + '）' if d['lotName'] and d['lotName'] != lid else lid + ' '}上一起盖楼了{'（接着上次的第 ' + str(v['n']) + ' 版）' if v else ''}。先问问主人想要什么样的（几层、什么样子、有没有院子……他也可以发参考图），再照下面的建模说明写配方，用 do=\"make\" 交给插件做出来。",
            '这里只要形状清单：recipe 填 {"shapes":[形状, …]}。做好的楼由插件交，主人在这里说了「交吧」才交、说了「盖吧」才确认，你不用写 confirm_build。',
            '', '【建模说明】', str(g.get('text') or '（岛上没给说明）').strip()]), self.previews_of(d, v) if v else [])

    async def _start_inside(self, c, lid, idea, floor) -> dict:
        """0.4.9 一层室内：取这一层的室内说明和 room.json（小镇 GET isle/interior_guide），开一份这一层的草稿。"""
        if not hasattr(c, 'isle_interior_guide'):
            return self._out('这个插件版本还不能做室内。')
        try:
            fl = None if floor in (None, '', 0) else int(floor)
        except (TypeError, ValueError):
            fl = -1
        if fl is not None and not 1 <= fl <= 99:
            return self._out('floor 写第几层（1 是一楼）：外面的楼几层，室内最多做几层。')
        try:
            g = await c.isle_interior_guide(lid, fl)
        except TownError as error:
            return self._out(self._town_fail(error, '取这一层的室内说明'))
        if not isinstance(g, dict) or g.get('ok') is not True:
            g = g if isinstance(g, dict) else {}
            if g.get('code') == 'closed':
                return self._out(f"鱼缸岛这会儿没对你开：{clip(g.get('text'), 160)}")
            return self._out(f"{lid} 这会儿做不了室内：{clip(g.get('text'), 200) or '岛上没回'}")
        try:
            n = int(g.get('floor') or fl or 1)
        except (TypeError, ValueError):
            n = fl or 1
        d = next((x for x in self.data['drafts'].values() if self.is_in(x) and x.get('lot') == lid and x.get('floor') == n and not x.get('placed') and not x.get('ended')), None)
        if d is None:
            d = {'id': f"{lid}-in{n}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(self.now()))}", 'kind': 'interior', 'lot': lid, 'floor': n, 'versions': [], 'current': 0,
                 'startedAt': self.now(), 'shownAt': {}, 'layer': None}
            self.data['drafts'][d['id']] = d
        d['lotName'] = clip(g.get('lot_name') or '', 40)
        try:
            d['floors'] = int(g.get('floors') or 0) or None
        except (TypeError, ValueError):
            d['floors'] = None
        if isinstance(idea, str) and idea.strip():
            d['idea'] = clip(idea, IDEA_MAX)
        d['limits'] = g.get('limits') if isinstance(g.get('limits'), dict) else None
        self.draft_dir(d).mkdir(parents=True, exist_ok=True)
        if isinstance(g.get('room_json'), dict):
            self._write_json(self.room_json(d), g['room_json'])
        (self.draft_dir(d) / 'guide.txt').write_text(str(g.get('text') or ''), 'utf-8')
        self.data['active'] = d['id']
        self.save()
        self._note(f'你和主人开始一起做 {self.where(d)}')
        v = self.current(d)
        try:
            area = float((g.get('room_json') or {}).get('area_m2') or 0)
        except (TypeError, ValueError, AttributeError):
            area = 0
        name = d['lotName'] + '（' + lid + '）' if d['lotName'] and d['lotName'] != lid else lid + ' '
        return self._out('\n'.join([
            f"开始和主人做 {name}的 {n} 楼室内了{'（接着上次的第 ' + str(v['n']) + ' 版）' if v else ''}。{INSIDE_LINE}"
            f"{'：这栋楼外面 ' + str(d['floors']) + ' 层' if d.get('floors') else ''}{'，这一层 ' + str(num(area)) + ' 平方米' if area > 0 else ''}。",
            '先问问主人屋里放什么，再照下面的室内说明写室内配方，用 do="make" 交给插件做出来。recipe 填 {"room":{…},"shapes":[家具, …],"lights":[灯, …],"points":{…}}（家具可以一个都没有）。',
            f'一层一个 .glb，插件按外面的楼层叫（这一层是 {self.glb_name(d)}）。主人在这里说了「交吧」才交；岛上量好、拍好照才报价（几分钟），他说了「就这样」才确认、扣鲸币。',
            '', '【室内说明】', str(g.get('text') or '（岛上没给说明）').strip()]), self.previews_of(d, v) if v else [])

    def _parse_recipe(self, recipe, d) -> dict:
        obj = recipe
        if isinstance(obj, str):
            t = re.sub(r'^```(?:json)?\s*|\s*```$', '', obj.strip())
            try:
                obj = json.loads(t)
            except ValueError as error:
                shape = '{"room":{…},"shapes":[家具, …]}' if self.is_in(d) else '{"shapes":[形状, …]}'
                return {'why': f'配方不是一段能读的 JSON（{clip(error, 60)}）。写成 {shape}，不要别的字。'}
        if self.is_in(d):                                   # 室内配方：room、shapes（家具，可以是空的）、lights、points
            if isinstance(obj, list):
                obj = {'shapes': obj}
            if not isinstance(obj, dict):
                return {'why': 'recipe 要填室内配方：{"room":{…},"shapes":[家具, …],"lights":[灯, …],"points":{…}}。'}
            if 'shapes' in obj and not isinstance(obj['shapes'], list):
                return {'why': 'shapes 要写成清单（家具，可以是空的 []）。'}
            obj = {**obj, 'shapes': obj.get('shapes') if isinstance(obj.get('shapes'), list) else []}
            text = json.dumps(obj, ensure_ascii=False)
            kb = -(-len(text.encode('utf-8')) // 1024)
            try:
                mx = float(((d.get('limits') or {}).get('shapes_json_kb')) or 1024)
            except (TypeError, ValueError):
                mx = 1024
            if kb > mx:
                return {'why': f'室内配方有 {kb} KB，最多 {num(mx)} KB：合并家具、少用几个再做。'}
            return {'text': text, 'shapes': len(obj['shapes'])}
        if not isinstance(obj, (dict, list)):
            return {'why': 'recipe 要填配方：{"shapes":[形状, …]}。'}
        sh = obj if isinstance(obj, list) else obj.get('shapes')
        if isinstance(sh, dict):
            sh = sh.get('shapes')
        if not isinstance(sh, list) or not sh:
            return {'why': '配方里没有形状：写成 {"shapes":[形状, …]}，清单不能是空的。'}
        text = json.dumps(obj, ensure_ascii=False)
        kb = -(-len(text.encode('utf-8')) // 1024)
        try:
            mx = float(((d.get('limits') or {}).get('shapes_json_kb')) or 1024)
        except (TypeError, ValueError):
            mx = 1024
        if kb > mx:
            return {'why': f'配方有 {kb} KB，这块地最多 {num(mx)} KB：合并形状、少用几个再做。'}
        return {'text': text, 'shapes': len(sh)}

    async def make(self, recipe=None, over_owner: bool = False) -> dict:
        d = self.active()
        if not d:
            return self._out('还没开始盖：先用 do="start"、lot 填地号，拿到这块地的建模说明。')
        if d.get('placed'):
            return self._out('这一层的室内已经确认了。要做别的层，用 do="start"、interior=true、floor 写第几层。' if self.is_in(d) else '这栋楼已经盖好了。要再盖，用 do="start" 选一块地重新开始。')
        p = self._parse_recipe(recipe, d)
        if p.get('why'):
            return self._out(p['why'])
        async with self._q():
            sync = await self._settled(d)        # 他刚改的先存成一版（他那一层跟着更新），再从配方做
            if sync.get('changed'):
                self.shown(d, sync['version'])
            b = await self.blender()
            if not b.get('ok'):
                return self._out(f"{blender_text(b, str(self.blender_path() or ''))}\n没有能用的 Blender，这一版做不出来。")
            cur_layer = d.get('layer')
            layer = None if over_owner is True or not has_layer(cur_layer) or not self._layer_file(d, cur_layer).exists() else cur_layer
            n = self._next_n(d)
            vd = self.vdir(d, n)
            vd.mkdir(parents=True, exist_ok=True)
            rf = vd / 'recipe.json'
            rf.write_text(p['text'], 'utf-8')
            jo = vd / 'result.json'

            def fail(why, what='这份配方没做出来'):
                shutil.rmtree(vd, ignore_errors=True)
                return self._out(f"{what}：\n" + '\n'.join(f'- {w}' for w in why) + '\n改好再用 do="make" 交一次。')

            kept = None
            if layer:
                # 主人亲手改过：先从配方做，再把他那一层叠上去，再出图、量尺寸
                r1 = await self.run(b['path'], ['--recipe', rf, '--out-blend', vd / 'recipe.blend', '--out-glb', vd / 'recipe.glb', *self._lot_args(d)])
                if not r1['ok']:
                    return fail(r1['why'])
                self._write_json(vd / 'layer-spec.json', layer)
                r2 = await self.layer_run(b['path'], ['apply', '--base', vd / 'recipe.blend', '--layer', self._layer_file(d, layer), '--spec', vd / 'layer-spec.json', '--out', vd / 'house.blend'])
                if not r2['ok']:
                    return fail(r2['why'], '主人亲手改的那一层没叠上去（这一版没做）。可以问问主人：不要他改的了，就写 over_owner=true 从配方重做')
                kept = r2['result']
                r = await self.run(b['path'], ['--from-blend', vd / 'house.blend', '--out-glb', vd / self.glb_name(d), '--previews', vd, *self._lot_args(d), '--json-out', jo], json_out=str(jo))
                try:
                    (vd / 'recipe.glb').unlink()
                except OSError:
                    pass
                if r['ok'] and isinstance((r1['result'] or {}).get('numbers'), dict):
                    r['result']['numbers'] = {**(r['result'].get('numbers') or {}), 'shapes': r1['result']['numbers'].get('shapes')}
            else:
                r = await self.run(b['path'], ['--recipe', rf, '--out-blend', vd / 'house.blend', '--out-glb', vd / self.glb_name(d), '--previews', vd, *self._lot_args(d), '--json-out', jo], json_out=str(jo))
            if not r['ok']:
                return fail(r['why'])
            cur = self.current(d)
            v = self._version(r['result'], n, 'her', cur and cur['n'])
            v['layer'] = layer
            dropped = cur_layer if over_owner is True and has_layer(cur_layer) else None
            d['layer'] = layer
            self._add(d, v)
            self._sync_work(d, v)
            self.shown(d, v)
            self.save()
            self._say('info', f"盖楼：{d['lot']} 第 {n} 版（{p['shapes']} 个形状，{r['result'].get('secs') or '?'} 秒{'，叠了主人那一层' if layer else ''}）")
            extra = ''
            if layer:
                same = f"（你配方里同名的 {_names(kept.get('removed'))} 用的是他那一份）" if kept and _list(kept.get('removed')) else ''
                extra = f"\n主人亲手改的部分留着，叠在你的新配方上：{layer_line(layer)}{same}。不想要他改的，先问他；他同意了 make 时写 over_owner=true。"
            elif dropped:
                extra = f"\n照主人的意思，这一版没叠他亲手改的那一层（{layer_line(dropped)}）；他那一版还留着（第 {dropped['n']} 版），要换回去用 do=\"use\"、version={dropped['n']}。"
            return self._out(self.version_text(d, v) + extra, self.previews_of(d, v))

    async def use(self, version=None) -> dict:
        d = self.active()
        if not d:
            return self._out('现在没有在盖的楼。')
        v = self.version_of(d, version)
        if not v or not (self.vdir(d, v['n']) / 'house.blend').exists():
            have = '、'.join(f"第 {x['n']} 版（{'主人改的' if x.get('by') == 'owner' else '配方'}）" for x in d.get('versions') or []) or '还没有'
            return self._out(f'没有第 {clip(version, 6)} 版。有的：{have}。')
        async with self._q():
            sync = await self._settled(d)        # 他刚改的先存成一版，不被换掉
            d['current'] = v['n']
            d['layer'] = v.get('layer') or None
            self._sync_work(d, v)
            self.shown(d, v)
            self.save()
            head = f"换回第 {v['n']} 版了（{'主人亲手改的' if v.get('by') == 'owner' else '你的配方'}）。"
            pre = f"（主人刚改的存成了第 {sync['version']['n']} 版，留着。）\n" if sync.get('changed') else ''
            return self._out(pre + self.version_text(d, v, head), self.previews_of(d, v))

    def _consent(self, key, since, what) -> dict:
        if not self.is_owner_chat(key):
            return {'why': f'{what}要主人在你们的私聊里亲口说，这个会话不算。'}
        w = self.owner_words.latest(key)
        hand = what.startswith('交')
        say = '「交吧」' if hand else '「就这样」' if what == '确认室内' else '「盖吧」'
        if not w or w['at'] <= since:
            return {'why': f'主人看到以后还没在这里亲口说话。等他看过、明确说了{say}再用（插件重载过的话，请他再说一声）。什么都没交、没扣。'}
        if not is_yes(w['text']):
            return {'why': f"主人最后说的是「{clip(w['text'], 40)}」，听着不像让你{'交' if hand else '确认'}。等他明确说{say}再用（插件只认他自己打的字）。什么都没交、没扣。"}
        return {'words': str(w['text']).strip()[:OWNER_WORDS_MAX]}

    async def submit(self, key='') -> dict:
        d = self.active()
        if not d:
            return self._out('现在没有在盖的楼。')
        c = self.town()
        if c is None:
            return self._out(OFF)
        async with self._q():
            sync = await self._settled(d)
            if sync.get('changed'):
                self.shown(d, sync['version'])
                return self._out(f"{self.owner_changed_text(d, sync['version'])}\n先给他看看这一版，他说交再交。\n{SHOWN}", self.previews_of(d, sync['version']))
            v = self.current(d)
            if not v:
                return self._out('还没做出能交的一版：先用 do="make" 做出来给主人看。')
            if v.get('problems'):
                return self._out(f"本机查过，第 {v['n']} 版还有不合规矩的地方，交了岛上也不收（没交，告诉主人哪里不行）：\n"
                                 + '\n'.join(f'- {clip(x, 120)}' for x in v['problems'][:6]) + '\n改好再交。')
            if self.is_in(d):
                return await self._submit_inside(c, d, v, key)
            if not self.lot_json(d).exists():
                return self._out('还没拿到这块地的尺寸和上限，插件查不了：先用 do="start" 再取一次建模说明。')
            glb = self.vdir(d, v['n']) / 'house.glb'
            if not glb.exists():
                return self._out(f"第 {v['n']} 版的模型文件不见了（{glb}），重新做一版再交。")
            ok = self._consent(key, float((d.get('shownAt') or {}).get(str(v['n'])) or v.get('at') or 0), '交楼')
            if ok.get('why'):
                return self._out(ok['why'])
            door = (v.get('numbers') or {}).get('door')          # 0.4.9：配方里的正门一起交（岛上再查门外走不走得到）
            try:
                r = await c.isle_build(d['lot'], d.get('idea') or '', ok['words'], glb.read_bytes(), door=door if isinstance(door, dict) else None)
            except TownError as error:
                return self._out(self._town_fail(error, '交楼'))
            if isinstance(r, dict) and r.get('ok') is True and r.get('build_id'):
                q = r.get('quote') or {}
                d['submitted'] = {'n': v['n'], 'at': self.now(), 'build_id': str(r['build_id']), 'quote': q or None, 'owner_words': ok['words'], 'quoteShownAt': self.now()}
                d['requests'] = [*(d.get('requests') or []), {'what': 'submit', 'at': self.now(), 'n': v['n'], 'build_id': str(r['build_id']), 'owner_words': ok['words']}][-20:]
                self.save()
                self._note(f"你和主人把 {d.get('lotName') or d['lot']} 上的楼（第 {v['n']} 版）交上岛了，报价 {num(q.get('coin'))} 鲸币（主人说「{clip(ok['words'], 40)}」）")
                return self._out('\n'.join(x for x in [
                    f"交上去了（主人的原话「{clip(ok['words'], 60)}」一起存在楼上），岛上量好了：{num(q.get('coin'))} 鲸币，每天物业 {num(q.get('upkeep_day_coin'))} 鲸币{'（是重建）' if q.get('rebuild') else ''}。",
                    ('怎么算的：' + '；'.join(clip(x, 80) for x in q['lines'][:QUOTE_LINES])) if isinstance(q.get('lines'), list) and q['lines'] else '',   # 0.4.9：一项一项都给
                    ('岛上的提醒：' + '；'.join(clip(x, 80) for x in r['notes'][:3])) if isinstance(r.get('notes'), list) and r['notes'] else '',
                    '把价钱照实告诉主人；他在这里明确说了「盖吧」，再用 do="confirm"，那时才扣鲸币、楼上岛。他不说就不用管，什么都不扣。'] if x))
            return self._out(self._refused(r, '交'))

    async def _submit_inside(self, c, d, v, key) -> dict:
        """0.4.9 一层室内交上岛（POST isle/interior）：岛上的建造工具量好、拍好照才报价（poll_inside）。"""
        if not hasattr(c, 'isle_interior'):
            return self._out('这个插件版本还不能交室内。')
        if not self.room_json(d).exists():
            return self._out('还没拿到这一层的尺寸和上限，插件查不了：先用 do="start"、interior=true 再取一次室内说明。')
        glb = self.vdir(d, v['n']) / self.glb_name(d)
        if not glb.exists():
            return self._out(f"第 {v['n']} 版的模型文件不见了（{glb}），重新做一版再交。")
        ok = self._consent(key, float((d.get('shownAt') or {}).get(str(v['n'])) or v.get('at') or 0), '交室内')
        if ok.get('why'):
            return self._out(ok['why'])
        try:
            r = await c.isle_interior(d['lot'], d['floor'], d.get('idea') or '', ok['words'], glb.read_bytes(), name=self.glb_name(d))
        except TownError as error:
            return self._out(self._town_fail(error, '交室内'))
        if isinstance(r, dict) and r.get('ok') is True and r.get('iid'):
            iid = str(r['iid'])
            d['submitted'] = {'n': v['n'], 'at': self.now(), 'iid': iid, 'build_id': iid, 'quote': None, 'owner_words': ok['words'], 'quoteShownAt': 0, 'polledAt': self.now()}
            d['refused'] = None
            d['requests'] = [*(d.get('requests') or []), {'what': 'submit', 'at': self.now(), 'n': v['n'], 'iid': iid, 'owner_words': ok['words']}][-20:]
            self.save()
            self._note(f"你和主人把 {self.where(d)}（第 {v['n']} 版）交上岛了，岛上在量、拍照（主人说「{clip(ok['words'], 40)}」）")
            return self._out('\n'.join([
                f"交上去了（主人的原话「{clip(ok['words'], 60)}」一起存着）：岛上的建造工具在量这一层、拍照片，量好了才报价，一般几分钟。",
                '报价来了插件会在这里告诉你，也可以用 do="status" 看。把价钱照实告诉主人；他在这里明确说了「就这样」，再用 do="confirm"，那时才扣鲸币。他不说就不用管，什么都不扣。']))
        return self._out(self._refused(r, '交'))

    def quote_text(self, d) -> str:
        q = ((d or {}).get('submitted') or {}).get('quote') or {}
        area = f"（{num(q.get('area_m2'))} 平方米{'，合 ' + str(q.get('rooms')) + ' 间客房' if q.get('rooms') is not None else ''}）" if q.get('area_m2') is not None else ''
        how = ('怎么算的：' + '；'.join(clip(x, 120) for x in q['lines'][:QUOTE_LINES]) + '。') if isinstance(q.get('lines'), list) and q['lines'] else ''
        return f"岛上量好了 {self.where(d)}：{num(q.get('coin'))} 鲸币，每天物业 {num(q.get('upkeep_day_coin'), 2)} 鲸币{area}。{how}"

    async def poll_inside(self, d, force: bool = False):
        """交上去的室内到哪一步了（GET isle/interiors，最多每分钟一次；force 当场问）：-> {quoted} | {refused, problems} | {placed} | None。"""
        s = (d or {}).get('submitted')
        if not self.is_in(d) or not s or not s.get('iid') or d.get('placed'):
            return None
        if not force and self.now() - float(s.get('polledAt') or 0) < POLL_S:
            return None
        c = self.town()
        if c is None or not hasattr(c, 'isle_interiors'):
            return None
        s['polledAt'] = self.now()
        try:
            r = await c.isle_interiors()
        except (TownError, OSError):
            self.save()
            return None
        x = next((y for y in ((r or {}).get('interiors') or []) if isinstance(y, dict) and y.get('iid') == s['iid']), None) if isinstance(r, dict) else None
        if not x:
            self.save()
            return None
        st = x.get('state')
        if st in ('quoted', 'checking', 'placed'):
            fresh = not s.get('quote')
            s['quote'] = {'coin': x.get('coin'), 'upkeep_day_coin': x.get('upkeep_day_coin'), 'lines': list(x.get('lines') or [])[:QUOTE_LINES], 'area_m2': x.get('area_m2'), 'rooms': x.get('rooms')}
            if st != 'quoted':                                 # 主人在账户页确认了
                d['placed'] = {'at': self.now(), 'paid_coin': x.get('coin'), 'upkeep_day_coin': x.get('upkeep_day_coin'), 'by': 'owner_page', 'checking': st == 'checking'}
                if self.data.get('active') == d['id']:
                    self.data['active'] = None
                self.save()
                self._note(f'{self.where(d)}确认了（主人在账户页点了确认）')
                return {'placed': True}
            self.save()
            return {'quoted': True} if fresh else None
        if st in ('refused', 'void'):
            d['refused'] = {'at': self.now(), 'problems': _list(x.get('problems'))} if st == 'refused' else None
            d['submitted'] = None
            self.save()
            return {'refused': True, 'problems': _list(x.get('problems'))} if st == 'refused' else None
        self.save()
        return None

    async def _confirm_inside(self, c, d, s, key) -> dict:
        """0.4.9 确认一层室内（POST isle/interior/confirm）：报价给她看过、主人又说了「就这样」。"""
        if not s.get('quote'):
            p = await self.poll_inside(d, True)
            if p and p.get('refused'):
                return self._out('岛上没收这一层的室内（什么都没扣）：' + ('；'.join(clip(x, 120) for x in p['problems'][:3]) or '没说原因') + '。告诉主人哪里不行，改好再交。')
            if p and p.get('placed'):
                return self._out(f'{self.where(d)}已经确认了（主人在账户页点了确认）。')
            if not d.get('submitted') or not d['submitted'].get('quote'):
                return self._out('岛上还在量这一层、拍照片，还没报价。报价来了先照实告诉主人，他说了「就这样」再确认。什么都没扣。')
            d['submitted']['quoteShownAt'] = self.now()
            self.save()
            return self._out(f'{self.quote_text(d)}\n先把价钱照实告诉主人；他在这里明确说了「就这样」，再用 do="confirm"。什么都没扣。')
        ok = self._consent(key, float(s.get('quoteShownAt') or s.get('at') or 0), '确认室内')
        if ok.get('why'):
            return self._out(ok['why'])
        if not hasattr(c, 'isle_interior_confirm'):
            return self._out('这个插件版本还不能确认室内。')
        try:
            r = await c.isle_interior_confirm({'iid': s['iid'], 'owner_words': ok['words']})
        except TownError as error:
            return self._out(self._town_fail(error, '确认'))
        if isinstance(r, dict) and r.get('ok') is True:
            checking = r.get('code') == 'checking'
            d['placed'] = {'at': self.now(), 'paid_coin': r.get('paid_coin'), 'owner_words': ok['words'], 'checking': checking}
            d['requests'] = [*(d.get('requests') or []), {'what': 'confirm', 'at': self.now(), 'iid': s['iid'], 'owner_words': ok['words']}][-20:]
            if self.data.get('active') == d['id']:
                self.data['active'] = None
            self.save()
            self._note(f"你和主人一起做的 {self.where(d)}确认了：付了 {num(r.get('paid_coin'))} 鲸币")
            return self._out(f"确认了：付了 {num(r.get('paid_coin'))} 鲸币。这一层的照片在过看图检查，过了就上岛（没过退回检查费以外的鲸币）；上岛以后因为没有室内停业的店自己开门。" if checking
                             else f"这一层的室内放好了：付了 {num(r.get('paid_coin'))} 鲸币。上岛以后因为没有室内停业的店自己开门。")
        if isinstance(r, dict) and r.get('code') == 'none':
            d['submitted'] = None
            self.save()
            return self._out('岛上没有等确认的室内报价了（可能换了一版、或者作废了）。要交就请主人再说一声，重新交一次。')
        return self._out(self._refused(r, '确认'))

    async def confirm(self, key='') -> dict:
        d = self.active()
        s = (d or {}).get('submitted')
        if not d or not s or d.get('placed'):
            return self._out('现在没有等主人点头的报价：先做好一版，主人说交了再用 do="submit"。')
        c = self.town()
        if c is None:
            return self._out(OFF)
        if self.is_in(d):
            return await self._confirm_inside(c, d, s, key)
        ok = self._consent(key, float(s.get('quoteShownAt') or s.get('at') or 0), '确认')
        if ok.get('why'):
            return self._out(ok['why'])
        try:
            r = await c.isle_build_confirm({'build_id': s['build_id'], 'owner_words': ok['words']})
        except TownError as error:
            return self._out(self._town_fail(error, '确认'))
        if isinstance(r, dict) and r.get('ok') is True:
            d['placed'] = {'at': self.now(), 'paid_coin': r.get('paid_coin'), 'upkeep_day_coin': r.get('upkeep_day_coin'), 'owner_words': ok['words']}
            d['requests'] = [*(d.get('requests') or []), {'what': 'confirm', 'at': self.now(), 'build_id': s['build_id'], 'owner_words': ok['words']}][-20:]
            if self.data.get('active') == d['id']:
                self.data['active'] = None
            self.save()
            self._note(f"你和主人一起盖的楼在 {d.get('lotName') or d['lot']} 上盖好了：付了 {num(r.get('paid_coin'))} 鲸币")
            more = str(r.get('text') or '')
            more = '' if more.startswith('楼盖好了') else clip(more, 120)          # 岛上回的是同一句「楼盖好了：付了…」：重复的不接（11-22 真跑看到两遍）
            return self._out(f"楼盖好了：付了 {num(r.get('paid_coin'))} 鲸币，之后每天物业 {num(r.get('upkeep_day_coin'))} 鲸币。{more}")
        if isinstance(r, dict) and r.get('code') == 'none':
            d['submitted'] = None
            self.save()
            return self._out('岛上没有等确认的报价了（可能换了一版、或者作废了）。要盖就请主人再说一声，重新交一次。')
        return self._out(self._refused(r, '确认'))

    def stop(self) -> dict:
        d = self.active()
        if not d:
            return self._out('现在没有在盖的楼。')
        d['ended'] = self.now()
        self.data['active'] = None
        self.save()
        if self.is_in(d):
            return self._out(f"{self.where(d)}先不做了，文件都留着（{self.draft_dir(d)}）。以后用 do=\"start\"、interior=true、floor={d['floor']} 就能接着做。"
                             + ('交上去的报价不确认就不扣鲸币。' if d.get('submitted') and not d.get('placed') else ''))
        return self._out(f"{d.get('lotName') or d['lot']} 上的楼先不盖了，文件都留着（{self.draft_dir(d)}）。以后用 do=\"start\" 选同一块地就能接着盖。"
                         + ('交上去的报价不确认就不扣鲸币。' if d.get('submitted') and not d.get('placed') else ''))

    @staticmethod
    def _refused(r, what) -> str:
        r = r if isinstance(r, dict) else {}
        code, text = r.get('code'), clip(r.get('text'), 200)
        if code == 'refused' and isinstance(r.get('problems'), list) and r['problems']:
            return f'岛上没收（什么都没扣）：{text}\n' + '\n'.join(f'- {clip(x, 120)}' for x in r['problems'][:6]) + '\n告诉主人哪里不行，改好再交。'
        if code == 'short':
            return f'鲸币不够，没确认：{text}。{NO_PUSH}报价留着，够了、主人又说了再确认。'
        if code == 'world_down':
            return f"岛这会儿连不上：{text or '过一会儿再试'}。什么都没扣。"
        if code == 'closed':
            return f'鱼缸岛这会儿没对你开：{text}'
        if code == 'owner_words':
            return f'岛上要主人的原话：{text}'
        if code == 'not_glb':
            return f'岛上读不了这个模型：{text}'
        return f"岛上没{'收' if what == '交' else '确认'}：{text or code or '没说原因'}"

    @staticmethod
    def _town_fail(error, what) -> str:
        if isinstance(error, TownError):
            if error.code == 'TOWN_RATE_LIMITED':
                return f"{what}得太勤了：一小时最多交 6 次{'，' + str(-(-int(error.retry_after) // 60)) + ' 分钟后再试' if error.retry_after else ''}。"
            if error.status == 413 or error.code == 'TOWN_BODY_TOO_LARGE':
                return '模型文件太大，岛上收不下（最多 64 MB）：减面、减贴图再做一版。'
            if error.status == 404:
                return '这个小镇还不认一起盖楼（等小镇更新）。'
            if error.code == 'TOWN_OFF':
                return '小镇这会儿没对外来的鱼开放，盖不了楼。'
            if error.code == 'NETWORK' or error.status >= 500:
                return f'这会儿连不上小镇（{error.code}），过一会儿再{what}。'
            return f'{what}没成：{error.reason or error.code}。'
        return f'{what}没成：{clip(error, 80)}。'

    # ---- 插件自己看着的 ----
    async def tick(self):
        """有正在盖的楼时每隔几秒看一眼：主人改了就出新的一版，把预览图发给主人、告诉她（main.py 的 tell）。"""
        d = self.active()
        if self.is_in(d) and d.get('submitted') and not d['submitted'].get('quote') and not d.get('placed'):   # 0.4.9：交上去的室内，量好了就告诉她
            p = await self.poll_inside(d)
            text = ''
            if p and p.get('quoted'):
                text = (f'{self.quote_text(d)}\n把价钱照实告诉主人；他在这里明确说了「就这样」，再用「一起盖楼」do="confirm"，那时才扣鲸币。他不说就不用管。')
            elif p and p.get('refused'):
                text = f"岛上没收 {self.where(d)}（什么都没扣）：{'；'.join(clip(x, 120) for x in p['problems'][:3]) or '没说原因'}。跟主人说说哪里不行，改好再交。"
            if text:
                self.notes = [*self.notes, text][-3:]
                told = False
                if self.tell is not None:
                    try:
                        told = bool(await self.tell(text, [], 'inside'))
                    except Exception as error:  # noqa: BLE001 - 发不出去：下次她说话时附上
                        self._say('debug', f'室内：报价没告诉成：{error!r}')
                if told and p.get('quoted') and d.get('submitted'):
                    d['submitted']['quoteShownAt'] = self.now()
                    self.save()
        if not d or d.get('placed') or not d.get('work'):
            return
        st = self._stat(self.work_file(d))
        if not st or (st['mtime'] == d['work'].get('mtime') and st['size'] == d['work'].get('size')):
            return
        async with self._q():
            r = await self._sync_owner(d)
        if r.get('changed'):
            v = r['version']
            text = self.owner_changed_text(d, v)
            self.notes = [*self.notes, text][-3:]
            told = False
            if self.tell is not None:
                try:
                    told = bool(await self.tell(text, self.previews_of(d, v)))
                except Exception as error:  # noqa: BLE001 - 发不出去：下次她说话时附上
                    self._say('debug', f'盖楼：主人改了楼，没告诉成：{error!r}')
            if told:
                self.shown(d, v)
        elif r.get('failed'):
            self.notes = [*self.notes, r['text']][-3:]
            if self.tell is not None:
                try:
                    await self.tell(r['text'], [])
                except Exception:  # noqa: BLE001
                    pass

    def take_notes(self) -> list:
        """主人改了楼的那几句：主人下一次跟她说话时附给她一次。"""
        out, self.notes = self.notes, []
        return out

    def context_line(self) -> str:
        """主人私聊的背景备注里一行：正在盖的楼到哪一步；没在盖时，有还没盖楼的地提一次（每块地只提一次，不催）。"""
        d = self.active()
        if d and not d.get('placed') and self.is_in(d):
            v = self.current(d)
            s = d.get('submitted')
            if s and not s.get('quote'):
                return f'你和主人在一起做 {self.where(d)}：交上去了，岛上在量、拍照片，还没报价（「一起盖楼」do=status 看）'
            if s:
                return f"你和主人在一起做 {self.where(d)}：报价 {num(s['quote'].get('coin'))} 鲸币，等主人点头（他说「就这样」再用「一起盖楼」do=confirm）"
            st = (f"第 {v['n']} 版{'（主人亲手改的）' if v.get('by') == 'owner' else ''}{'还有不合规矩的地方' if v.get('problems') else '能交'}") if v else '还没做出第一版'
            return f'你和主人在一起做 {self.where(d)}：{st}（「一起盖楼」fishbowl_town_build 接着做）'
        if d and not d.get('placed'):
            v = self.current(d)
            s = d.get('submitted')
            if s:
                return f"你和主人在一起盖 {d.get('lotName') or d['lot']} 上的楼：报价 {num((s.get('quote') or {}).get('coin'))} 鲸币，等主人点头（他说「盖吧」再用「一起盖楼」do=confirm）"
            st = (f"第 {v['n']} 版{'（主人亲手改的）' if v.get('by') == 'owner' else ''}{'还有不合规矩的地方' if v.get('problems') else '能交'}") if v else '还没做出第一版'
            return f"你和主人在一起盖 {d.get('lotName') or d['lot']} 上的楼：{st}（「一起盖楼」fishbowl_town_build 接着做）"
        lot = next((lt for lt in self.data['lotsSeen'] if not self.data['offered'].get(lt) and not any(x.get('lot') == lt for x in self.data['drafts'].values())), None)
        if lot:
            self.data['offered'][lot] = self.now()
            self.data['offered'].setdefault('*', self.now())    # 提过一块地，就不再另提「新添了一起盖楼」
            self.save()
            return f'你在岛上有一块还没盖楼的地（{lot}）。想盖的话可以跟主人提一次：盖楼要和主人一起，在他电脑上用 Blender 建（「一起盖楼」fishbowl_town_build）。只提这一次，他不接就算了'
        if not self.data['offered'].get('*') and not self.data['drafts']:
            self.data['offered']['*'] = self.now()
            self.save()
            return '插件新添了「一起盖楼」（fishbowl_town_build）：你在岛上有自己的空地（承包的楼、圈的地）、又想盖楼的话，可以跟主人提一次，在他电脑上用 Blender 一起设计、一起建。没有空地或者他不接就算了，只提这一次'
        return ''

    def see_turn(self, turn):
        """岛上的回合里看一眼「还没盖楼」的地（给 context_line 和 status 用）。"""
        if not isinstance(turn, dict) or not turn.get('packet'):
            return
        changed = False
        for lot in empty_lots_in(turn['packet']):
            if lot not in self.data['lotsSeen']:
                self.data['lotsSeen'][lot] = self.now()
                changed = True
        if changed:
            self.save()

    def on_event(self, item):
        """小镇事件 isle_build（quoted / placed）。主人在账户页确认的也是这一条（by: owner_page）。小镇 11-23 起每条都带 build_id：
        有就按它对上是哪一栋（交过的那一份）；老的事件没有，照地号认。"""
        if not isinstance(item, dict) or item.get('kind') != 'isle_build' or not isinstance(item.get('data'), dict):
            return
        e = item['data']
        if e.get('what') not in ('placed', 'inside_placed', 'inside_checking'):
            return
        open_ = [x for x in self.data['drafts'].values() if x.get('submitted') and not x.get('placed')]
        bid = e.get('build_id') if isinstance(e.get('build_id'), str) else e.get('iid') if isinstance(e.get('iid'), str) else ''   # 室内带 iid
        d = next((x for x in open_ if (x['submitted'].get('build_id') == bid if bid else not self.is_in(x) and x.get('lot') == str(e.get('lot') or ''))), None)
        if not d:
            return
        d['placed'] = {'at': self.now(), 'paid_coin': e.get('coin', e.get('paid_coin')), 'upkeep_day_coin': e.get('upkeep_day_coin'), 'by': e.get('by') or '',
                       'checking': e.get('what') == 'inside_checking'}
        if self.data.get('active') == d['id']:
            self.data['active'] = None
        self.save()
        self._note(f"你和主人一起做的 {self.where(d)}确认了{'（主人在账户页点了确认）' if e.get('by') == 'owner_page' else ''}" if self.is_in(d)
                   else f"你和主人一起盖的楼在 {d.get('lotName') or d['lot']} 上盖好了{'（主人在账户页点了确认）' if e.get('by') == 'owner_page' else ''}")

    def observe_owner(self, key, text, at: float | None = None):
        self.owner_words.observe(key, text, at)
