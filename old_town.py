"""0.4.3 她住在鱼缸岛上（主人 11-21：每条鱼都搬到岛上住；老镇——鱼缸小镇——这周照常，11 月 28 日关门；岛上的路是真的，
去哪都要走过去，到了才能做那儿的事；「主人不能操控自己鱼」；岛上一律用鲸币）。和 DSH 插件 lib/old-town.js 同一套话。
能用小镇自己的就用小镇的（回合里的 packet.isle 和处境、status 的 isle {open, here, hint}、now_line）；插件只补「住在岛上、路要走、
老镇哪天关」这几句，岛上以后加的东西（约好地点和时间的事……）跟着小镇的话走，不用再发插件。"""
from __future__ import annotations

import re
import time

# 0.4.13（主人 11-24「就这么做」：老镇 11-25 凌晨起不再叫醒鱼、不再给鱼选，11-28 整个关掉）：这一版 11-25 发，发出去时老镇已经不给鱼选了——
# 给她的话里不再有「老镇这周还能去」那句。老镇讲不讲跟着小镇走（oldtown-close 11-24：服务器的开关拨得回去，插件别写死）：
# 小镇这一回合列着老镇的动作才讲；11-28 整个关掉以后一律不讲。和 DSH 0.9.17 同一套。
CLOSE_AT = 1795795200            # 2026-11-28 00:00 北京时间：老镇整个关掉
# 老镇的动作（小镇在拿主意的回合里列出来的；老镇停了小镇就不列了）
OLD_ACTIONS = {'signup', 'cancel', 'visit', 'join', 'leave', 'pier', 'arcade', 'make', 'court', 'challenge', 'propose', 'club_join', 'club_leave'}
# 0.4.13 老镇安静（小镇 old_quiet）时回合里还会有：cancel / leave / club_leave（退出照常），visit（约好的照走：小手机约见到点、
# 今天到期的约定、第一次约会）。这几样不算「老镇还在给她选」，只有开新的那几样才算（小镇的开关拨回去，就又列出来了）。
OLD_NEW = OLD_ACTIONS - {'cancel', 'leave', 'club_leave', 'visit'}
ISLE_HOME = '你住在鱼缸岛上。岛上去哪都得走过去（多远、几分钟，小镇会写给你），到了才能做那儿的事；约了地方就早点动身。'
CLOSED = '老镇（鱼缸小镇）关了，这一块去不了了。'
CLOSED_HER = CLOSED + '你住在鱼缸岛上：岛上想去哪、做什么，用「去小镇」写下你的打算就行。'
CLOSED_OWNER = CLOSED + '她住在鱼缸岛上，岛上去哪、做什么由她自己定；想让她去哪儿，就在聊天里跟她说。'


def old_town_open(now: float | None = None) -> bool:
    return (time.time() if now is None else now) < CLOSE_AT


def _packet(turn) -> dict:
    p = (turn or {}).get('packet') if isinstance(turn, dict) else None
    return p if isinstance(p, dict) else {}


def on_isle(turn) -> bool:
    """这一回合她在鱼缸岛上（小镇带了 packet.isle）。"""
    return isinstance(_packet(turn).get('isle'), dict)


def old_town_offered(turn, now: float | None = None) -> bool:
    """老镇还开着，而且小镇这一回合还列着老镇开新的动作（0.4.13：没列动作就是没有；只列退出、约好的串门不算）。"""
    acts = _packet(turn).get('actions')
    return old_town_open(now) and isinstance(acts, list) and any(a in OLD_NEW for a in acts)


def isle_lines(turn) -> list:
    """拿主意的提示里那一句：在岛上就说住在岛上、路要走（0.4.13：不再说老镇哪天关）。"""
    return [ISLE_HOME] if on_isle(turn) else []


def closed_text(error=None, *, owner: bool = False, now: float | None = None) -> str:
    """老镇的接口回 TOWN_OFF / 404：关门以后照实说关了、指向岛上；关门以前只说这一项这会儿没开。"""
    if old_town_open(now):
        why = str(getattr(error, 'reason', '') or '').strip().rstrip('。.')
        return f"这一项这会儿没开放{'：' + why if why else ''}。"
    return CLOSED_OWNER if owner else CLOSED_HER


def isle_status_line(st) -> str:
    """/town status 里一行：小镇 status 的 isle {open, here}。小镇没给就不写。"""
    i = st.get('isle') if isinstance(st, dict) else None
    if not isinstance(i, dict):
        return ''
    if i.get('open') is False:
        return '鱼缸岛：这会儿没对她开放'
    if i.get('here') is True:
        return '鱼缸岛：她在岛上'
    if i.get('here') is False:
        return '鱼缸岛：她这会儿不在岛上'
    return ''


IDLE_NOW = re.compile(r'^在家歇着')


def now_with_isle(st) -> str:
    """「你现在」那一句：小镇的 now_line；她在岛上、now_line 只是老镇那句「在家歇着，没在镇上哪个地方」时，说她在鱼缸岛上。"""
    line = str((st or {}).get('now_line') or '').strip() if isinstance(st, dict) else ''
    i = st.get('isle') if isinstance(st, dict) else None
    if isinstance(i, dict) and i.get('here') is True and (not line or IDLE_NOW.match(line)):
        return '在鱼缸岛上'
    return line
