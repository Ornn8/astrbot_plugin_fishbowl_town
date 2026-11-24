"""插件自己更新（0.3.2，主人 11-17：「astrbot插件不能推送更新通知和进行更新？」）。

AstrBot 的插件页只给「从插件市场 / 仓库地址装的」插件做更新检测；从我们官网下 zip 上传的（大多数人）看不到新版本，也点不了更新。
所以插件自己看：每 CHECK_EVERY 读一次官网的 latest.json（版本、zip 地址、sha256、一句话说明），

* 有新版本：在主人私聊里说一次（每个新版本一次）；主人发 /town update（管理员）就更新；
* 配置 auto_update 打开：直接更新，更新完说一声。

更新 = 下载 zip、核对 sha256、交给 AstrBot 自己的「上传安装」（同名插件原地覆盖，插件配置和数据目录都不动），AstrBot 重载插件。
只认我们自己的地址（ALLOWED），别处来的地址一律不下。"""
from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path

import aiohttp

LATEST_URL = 'https://fishbowltown.com/dsh/pkg/astrbot/latest.json'
ALLOWED = ('https://fishbowltown.com/dsh/pkg/astrbot/', 'https://github.com/Ornn8/astrbot_plugin_fishbowl_town/releases/download/')
CHECK_EVERY = 6 * 3600.0
FIRST_CHECK = 120.0            # the first look after a start (the town connection goes first)
ZIP_MAX = 5 * 1024 * 1024


def parse(version) -> tuple | None:
    if not isinstance(version, str) or not re.fullmatch(r'\d+(?:\.\d+){1,3}', version.strip()):
        return None
    return tuple(int(x) for x in version.strip().split('.'))


def newer(theirs, mine) -> bool:
    a, b = parse(theirs), parse(mine)
    return a is not None and b is not None and a > b


def clean(info) -> dict | None:
    """latest.json -> {version, url, sha256, notes}, or None when it is not ours / not complete."""
    if not isinstance(info, dict) or parse(info.get('version')) is None:
        return None
    url, sha = str(info.get('url') or ''), str(info.get('sha256') or '').lower()
    if not url.startswith(ALLOWED) or not url.endswith('.zip') or not re.fullmatch(r'[0-9a-f]{64}', sha):
        return None
    return {'version': info['version'].strip(), 'url': url, 'sha256': sha, 'notes': str(info.get('notes') or '').strip()[:120]}


async def fetch_latest(session: aiohttp.ClientSession | None = None, url: str = LATEST_URL) -> dict | None:
    own = session is None
    session = session or aiohttp.ClientSession()
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15), headers={'Cache-Control': 'no-cache'}) as res:
            if res.status != 200:
                return None
            return clean(await res.json(content_type=None))
    except (aiohttp.ClientError, ValueError, asyncio.TimeoutError):
        return None
    finally:
        if own:
            await session.close()


CHUNK = 64 * 1024


async def _read_all(res) -> bytes:
    """The whole body, chunk by chunk, stopping past ZIP_MAX. (0.4.13: ``res.content.read(n)`` is 「at most n」: it returned what was in
    the buffer, the first chunk only, so the sha256 never matched and no self-update succeeded since 0.3.2.)"""
    data = bytearray()
    async for chunk in res.content.iter_chunked(CHUNK):
        data += chunk
        if len(data) > ZIP_MAX:
            break
    return bytes(data)


async def download(info: dict, folder: Path, session: aiohttp.ClientSession | None = None) -> Path:
    """The zip into ``folder``, sha256 checked. Raises RuntimeError with a reason a person can read."""
    own = session is None
    session = session or aiohttp.ClientSession()
    try:
        async with session.get(info['url'], timeout=aiohttp.ClientTimeout(total=120)) as res:
            if res.status != 200:
                raise RuntimeError(f'下载新版失败（HTTP {res.status}）')
            data = await _read_all(res)
            want = res.content_length
            plain = str((res.headers or {}).get('Content-Encoding') or 'identity').lower() == 'identity'
    except aiohttp.ClientError as error:
        raise RuntimeError(f'下载新版失败（{type(error).__name__}）') from error
    except asyncio.TimeoutError as error:
        raise RuntimeError('下载新版超时了') from error
    finally:
        if own:
            await session.close()
    if len(data) > ZIP_MAX:
        raise RuntimeError('下载到的文件太大，不像插件包，没有装')
    if want is not None and plain and len(data) != want:                   # (压缩传的：长度是压缩后的，不比)
        raise RuntimeError(f'下载没下完（收到 {len(data)} / {want} 字节），没有装，过会儿再试')
    if hashlib.sha256(data).hexdigest() != info['sha256']:
        raise RuntimeError('下载到的文件和官网写的校验值对不上，没有装（可能被网络改过，过会儿再试）')
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"update-{info['version']}.zip"
    path.write_bytes(data)
    return path


async def install(context, zip_path: Path) -> None:
    """AstrBot's own upload install (same plugin name: replaced in place, its config kept), then AstrBot reloads it."""
    manager = getattr(context, '_star_manager', None)
    if manager is None or not hasattr(manager, 'install_plugin_from_file'):
        raise RuntimeError('这个版本的 AstrBot 不让插件自己更新，请在 AstrBot 插件页上传新的 zip')
    await manager.install_plugin_from_file(str(zip_path))


def manual_text(info: dict) -> str:
    """自己更新没成时给主人的手动那条路（0.4.13）。"""
    return (f"手动更新：下载 {info['url']}，打开 AstrBot 管理面板的插件页 → 点「安装插件」→「从文件安装」→「选择文件」，选这个 zip（同名插件原地覆盖，设置和数据都保留；别用「从链接安装」，那里只认 Git 仓库地址）。")


def told_text(info: dict, mine: str) -> str:
    notes = f"这一版：{info['notes']}" if info.get('notes') else ''
    return (f"有新版本 {info['version']}（现在是 {mine}）。在这里发 /town update 就更新（设置都保留），"
            f"或者在插件配置里打开「自动更新」。{notes}")
