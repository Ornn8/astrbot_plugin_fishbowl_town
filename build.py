"""0.4.4 建模回合（DSH 插件 lib/build.js 的 Python 版；主人 11-21「建模回合加上吧，插件说明里面记得弄，不然鱼不知道怎么弄，包括之前的建模工具」）。
鱼缸岛上的楼由她自己用她的模型建模（一份形状清单 JSON）。拿主意的回合里她写 {"world":{"do":"build","lot":"L-0107","idea":"…"}}，
小镇另给她一个 kind=build 的回合：消息 = 这块地的建模说明（尺寸、地界、形状、材料、上限、怎么算钱）+ 她的想法；
答 = 她的模型 {"kind":"shapes","shapes":{"shapes":[…]}}（或 {"kind":"recipe",…}），不盖了就 {"skip":true}。最多 8000 字，时限 120 秒（回合里带着）。
只有轮询写了 kinds=activity,build 的程序才收到这种回合（town_client.py）；0.4.3 和更早的插件收不到。
她的模型没答上、答得读不了：交 {"skip":true}，这次什么都不盖——插件和小镇都不替她建模，她以后可以再写一次想法。
交了以后小镇量尺寸、报价；她下一次拿主意时写 {"world":{"do":"confirm_build","build_id":"…"}} 才扣鲸币、楼上岛。
只讲怎么交，不讲长什么样（主人 11-20「不要带风格化的提示词」）。"""
from __future__ import annotations

import json

from .prompts import extract_json, text_of

BUILD_KIND = 'build'
BUILD_WITHIN_S = 120          # 小镇没写时限时按 120 秒
BUILD_WITHIN_MAX_S = 180
BUILD_MAX_CHARS = 8000
SKIP = '{"skip":true}'


def _packet(turn) -> dict:
    p = turn.get('packet') if isinstance(turn, dict) else None
    return p if isinstance(p, dict) else {}


def is_build(turn) -> bool:
    return isinstance(turn, dict) and turn.get('kind') == BUILD_KIND


def build_within_s(turn) -> float:
    """建模回合的时限（秒）：回合里写的（packet.isle.within_s / packet.within_s），没写就 120 秒；不是建模回合回 0。"""
    if not is_build(turn):
        return 0
    p = _packet(turn)
    isle = p.get('isle') if isinstance(p.get('isle'), dict) else {}
    for v in (isle.get('within_s'), p.get('within_s')):
        try:
            s = float(v or 0)
        except (TypeError, ValueError):
            continue
        if s > 25:
            return min(BUILD_WITHIN_MAX_S, s)
    return BUILD_WITHIN_S


def build_max_chars(turn) -> int:
    """她的回答最多多少字（回合的 reply.max_chars，没写就 8000）。"""
    reply = _packet(turn).get('reply') if isinstance(_packet(turn).get('reply'), dict) else {}
    try:
        n = int(reply.get('max_chars') or 0)
    except (TypeError, ValueError):
        n = 0
    return n if n > 0 else BUILD_MAX_CHARS


def build_budget(turn) -> int:
    """这一回合她的输出大约有多少 token 的余量（字数的一半，约 4000）。只写进提示里：AstrBot 的 llm_generate 不把输出上限带给模型。"""
    return max(500, round(build_max_chars(turn) / 2))


def build_prompt(turn, *, seconds_left: int = 0) -> str:
    """建模回合交给她的那一问。小镇的建模说明原样给她；插件只说怎么交、交了以后怎样。"""
    p = _packet(turn)
    messages = p.get('messages') if isinstance(p.get('messages'), list) else []
    system = '\n\n'.join(text_of(m.get('content')) for m in messages if isinstance(m, dict) and m.get('role') == 'system')
    user = '\n\n'.join(text_of(m.get('content')) for m in messages if isinstance(m, dict) and m.get('role') != 'system')
    mx = build_max_chars(turn)
    as_ = f"你在岛上叫「{p['as']}」" if p.get('as') else ''
    left = f"{' · ' if as_ else ''}{seconds_left} 秒内回答" if seconds_left else ''
    return '\n'.join([
        f'【鱼缸岛 · 建模回合】{as_}{left}',
        '你拿主意时说了想在自己的地上盖楼。这一回合只做一件事：照下面的建模说明，用你自己的想法把这栋楼写成一份形状清单，交给镇上量尺寸、报价。长什么样你自己定。',
        '',
        '【建模说明】', system.strip() or '（无）', '',
        '【这块地和你的想法】', user.strip() or '（无）', '',
        '【怎么回答】',
        f'只输出一段 JSON（不超过 {mx} 字），第一个字符就是 {{，不要代码块、不要别的文字：{{"kind":"shapes","shapes":{{"shapes":[形状, …]}}}}（配方就写 "kind":"recipe"，照说明里的写法）。',
        f'字数有限：{mx} 字大约是几十个形状，写不下就合并、少用几个。做好的 .glb 文件这边交不了。',
        '这一回合写不出来、改主意了，就只写 {"skip":true}：什么都不盖，以后想盖再写一次想法。',
        '交了以后镇上量尺寸、出报价；你下次拿主意时看到报价，写 {"world":{"do":"confirm_build","build_id":"…"}} 才扣鲸币、楼上岛，不确认就不扣。',
    ])


def read_build(turn, raw) -> dict:
    """她的回答 -> {answer, why, shapes, chars[, skip]}。answer 是交给小镇的字符串（她的模型或 SKIP），读不了是 None：
    why = 'empty'（没说话）| 'unreadable'（不是 JSON，多半写到一半断了）| 'no_model'（JSON 里没有形状清单）| 'long'（超过字数）。
    只整理格式（少写了 kind、形状清单少包了一层），不改她的形状、不替她补。"""
    text = str(raw or '').strip()
    if not text:
        return {'answer': None, 'why': 'empty', 'shapes': 0, 'chars': 0}
    obj = extract_json(text)
    if obj is None:
        return {'answer': None, 'why': 'unreadable', 'shapes': 0, 'chars': len(text)}
    if obj.get('skip') is True or obj.get('skip') == 'true':
        return {'answer': SKIP, 'why': '', 'skip': True, 'shapes': 0, 'chars': len(SKIP)}
    out = dict(obj)
    if isinstance(out.get('shapes'), list):
        out['shapes'] = {'shapes': out['shapes']}
    lst = out['shapes'].get('shapes') if isinstance(out.get('shapes'), dict) and isinstance(out['shapes'].get('shapes'), list) else None
    if not out.get('kind') and lst is not None:
        out['kind'] = 'shapes'
    if not lst or not isinstance(out.get('kind'), str):
        return {'answer': None, 'why': 'no_model', 'shapes': 0, 'chars': len(text)}
    answer = json.dumps(out, ensure_ascii=False, separators=(',', ':'))
    if len(answer) > build_max_chars(turn):
        return {'answer': None, 'why': 'long', 'shapes': len(lst), 'chars': len(answer)}
    return {'answer': answer, 'why': '', 'shapes': len(lst), 'chars': len(answer)}


# 没交上的原因（记进她的本子：下次拿主意时她看得到那次不是她不想盖；小镇那边只知道 skip）
BUILD_WHY = {'model': '你的模型没在时限内答完', 'invalid': '交的答案读不了', 'error': '你的模型这次出错了', 'late': '答好时已经过了时限'}


def build_note(turn, *, why: str = '', shapes: int = 0, skip: bool = False) -> str:
    """记进她本子的一行：交了什么、这次不盖、没交上和为什么。"""
    lot = str(_packet(turn).get('lot') or '')[:16]
    where = f'（地 {lot}）' if lot else ''
    if skip:
        return f'小镇给你的建模回合{where}你这次没交，什么都没盖'
    if why:
        return f'小镇给你的建模回合{where}没交上（{BUILD_WHY.get(why, why)}），这次什么都没盖；想盖再写一次想法'
    return f'你在建模回合{where}交了一份 {shapes} 个形状的楼，等镇上量尺寸、报价；报价出来后写 confirm_build 才扣鲸币'


def build_again(why: str, *, max_chars: int = BUILD_MAX_CHARS, chars: int = 0) -> str:
    """第一次的回答交不了：追问一次（时间够才问）。"""
    tail = f'只输出那一段 JSON，第一个字符就是 {{，不超过 {max_chars} 字；不想盖了就只写 {{"skip":true}}。'
    if why == 'long':
        return f'刚才的 JSON 有 {chars} 字，超过了 {max_chars} 字，镇上收不下。合并能合并的形状、少用几个，再写一次：{tail}'
    if why == 'no_model':
        return f'刚才的回答里没有形状清单。要写成 {{"kind":"shapes","shapes":{{"shapes":[形状, …]}}}}，清单不能是空的：{tail}'
    return f'刚才的回答不是一段能读的 JSON（多半是写到一半断了）。写不完就少用几个形状：{tail}'
