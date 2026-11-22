"""她在小镇的本子（DSH 插件 journal.js + schedule.js 的 Python 版）：小镇那边拿的主意、小镇传回来的事、主人托的话、守则、
先停住的事、她自己定的作息。存成插件数据目录里的一个 JSON 文件。"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from .prompts import GAME_ZH, HOLD_KINDS

KEEP = 400                 # 0.3.3：小镇的事全部进聊天（主人 11-17「我要的是全部内容」），多记一些（40 -> 400）
WISH_FOR = 2 * 3600
RULES_MAX = 500
HELD_KEEP = 8
WAKE_MIN, WAKE_MAX = 5, 480
BJ = 8 * 3600
# DSH 插件 0.7.11（11-16 一条鱼跟主人说「我从头到尾没被叫出过一次牌」：那局小镇那边替她答了 10 手，另 16 手超时由小镇代打，
# 聊天里的她只拿到一句「你是农民，赢了」）：一局里那边替她出的每一手、超时几手，记下来，那局打完时连同结果给聊天里的她
TABLES_KEEP = 6
MOVES_KEEP = 30

ACTION_ZH = {'none': '什么都不做，歇着', 'signup': '报名', 'cancel': '取消报名', 'visit': '去串门', 'join': '参加活动', 'leave': '离开活动',
             'pier': '去码头', 'arcade': '去电玩城', 'make': '去做点东西', 'court': '去旁听', 'challenge': '下战书', 'propose': '提议一件事',
             'club_join': '加入俱乐部', 'club_leave': '退出俱乐部'}
TOWN_DECIDED = '你在小镇定了'
# 照实说是谁做的：小镇的回合是插件另调她的模型答的，她在聊天里不在场；那些选择记在她名下，但不算她的判断
# 0.1.2（DSH 0.7.13 同一件事：一条鱼说「能自由的地方基本就一处：能说『不去』」，四天里没用过一次「去小镇」写自己的打算）：
# 照实告诉她，去哪、做什么她自己也能定，那边照着去做
# 0.2.0（主人 11-17：用户老说小镇里的是「分身」；DSH 0.8.1 同一件事）：小镇里的就是她本人，不再说「你当时不在场 / 不算你的判断」
TOWN_WHO = ('小镇里的话和事就是你自己在小镇做的：插件另外调你的模型、用你的人设一步步替你答（打牌、串门一轮只有二十几秒，所以不在聊天里刷屏），'
            '那边的你和这边的你是同一个你，事后都记在这里；花的钱、答应的事都是你的。'
            '主人问小镇里那个是不是你、说你在小镇是分身时：就是你，别说「那不是我」；那边答得快，有时和你在这儿想的不一样，可以说当时没想周全、以后照你的打算来。'
            '主人想让小镇的事回到这个私聊，就用 fishbowl_town_here 把你在小镇的家搬到这儿。'
            '去哪、做什么由你自己定：想去岛上哪儿、找谁、做点什么，就用 fishbowl_town_go 写下你的打算（plan），插件马上请那边照着去做。'
            '想让那边以后怎么替你做，用 fishbowl_town_rules 留守则（也能让某类事先停住等你一句）。认不认、要不要补一句或改口，由你。')
# 必须她自己回答的拿主意理由（她歇着也叫醒）；没什么可做的理由
MUST_ANSWER = {'wish', 'heart', 'invited', 'promise_due', 'challenged', 'pulled', 'meet_ask'}
IDLE_REASONS = {'idle', 'fcode', 'ad', 'venue'}
# 0.4.5：小镇到了她自己定的时间（她的 next_wake）来叫她：reasons 里带 'wake'（可能只有它）。这是她自己的一次拿主意，一定叫她的模型，
# 不当成「没什么可做的」、也不当成她还在歇着（歇到的就是这会儿）；主人定的勿扰时段照样算（和 DSH 插件 0.9.8 一样）
WAKE = 'wake'


def short(s, n: int) -> str:
    s = re.sub(r'\s+', ' ', str(s or '')).strip()
    return s[:n] + '…' if len(s) > n else s


def clock(ts: float) -> str:
    return time.strftime('%m-%d %H:%M', time.gmtime(ts + BJ))


def describe_decision(d: dict, who: str = '你决定') -> str:
    what = ACTION_ZH.get(d.get('action'), d.get('action') or '什么都不做')
    obj = ' · '.join(short(x, 20) for x in (GAME_ZH.get(d.get('game'), d.get('game')), d.get('target'), d.get('place')) if x)
    why = d.get('note') or d.get('intent')
    say = f"，说：「{short(d['say'], 50)}」" if d.get('say') and d.get('say') != why else ''
    wake = f"；打算 {d['next_wake']} 分钟后再看看" if d.get('next_wake') else ''
    return f"{who}：{what}{f'（{obj}）' if obj else ''}{f'——「{short(why, 60)}」' if why else ''}{say}{wake}"


def play_of(turn: dict, answer) -> str:
    """交给小镇的一手（JSON 字符串或 dict）-> 这一手出的是什么（写了 cards 就是 cards；只选了字母就到局面里找那个选项的字）。"""
    try:
        a = json.loads(answer) if isinstance(answer, str) else answer
    except ValueError:
        return ''
    if not isinstance(a, dict):
        return ''
    if a.get('cards') is not None and str(a['cards']).strip():
        return short(a['cards'], 20)
    pick = str(a.get('pick') or '').strip().upper()
    if re.fullmatch(r'[A-Z0-9]{1,3}', pick):
        msgs = ((turn or {}).get('packet') or {}).get('messages') or []
        text = '\n'.join(str(m.get('content') or '') for m in msgs if isinstance(m, dict) and m.get('role') == 'user')
        m = re.search(rf'^\s*{pick}[.．、:：]\s*(.+?)\s*(?:—|－|--|$)', text, re.M)
        return short(m.group(1) if m else f'选了{pick}', 20)
    for k in ('tile', 'discard', 'vote', 'target', 'word', 'guess', 'question', 'action', 'choice', 'bid'):
        v = a.get(k)
        if v is not None and not isinstance(v, (dict, list)) and str(v).strip():
            return short(v, 20)
    return ''


def parse_quiet(v):
    m = re.match(r'^(\d{1,2})[:：](\d{2})\s*[-–—~到至]\s*(\d{1,2})[:：](\d{2})$', str(v or '').strip())
    if not m:
        return None
    a, b = int(m[1]) * 60 + int(m[2]), int(m[3]) * 60 + int(m[4])
    if a > 1440 or b > 1440 or a == b:
        return None
    return a % 1440, b % 1440


def _bj_minute(ts: float) -> int:
    return int(((ts + BJ) % 86400) // 60)


def in_quiet(q, ts: float) -> bool:
    if not q:
        return False
    m = _bj_minute(ts)
    return q[0] <= m < q[1] if q[0] < q[1] else m >= q[0] or m < q[1]


def quiet_left(q, ts: float) -> float:
    """勿扰时段从 ts 起还剩多少分钟（不在勿扰时段 = 0）。"""
    if not in_quiet(q, ts):
        return 0
    return ((q[1] - _bj_minute(ts)) % 1440 or 1440) - (ts % 60) / 60


def clamp_min(n: float) -> int:
    return max(WAKE_MIN, min(WAKE_MAX, round(n)))


class Journal:
    def __init__(self, path: Path, now=time.time):
        self.path = Path(path)
        self.now = now
        try:
            self.s = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.s = {}
        self.s.setdefault('journal', [])
        self.s.setdefault('wake', {})

    def save(self):
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.s, ensure_ascii=False), encoding='utf-8')
        os.replace(tmp, self.path)

    def _add(self, e: dict):
        self.s['journal'].insert(0, {'ts': self.now(), **e})
        del self.s['journal'][KEEP:]
        self.save()

    # --- 小镇那边拿的主意、小镇传回来的事 -------------------------------------------------------------------------------
    def decision(self, a: dict):
        d = {'kind': 'decision', 'action': str(a.get('action') or 'none'), 'game': str(a.get('game') or ''), 'target': str(a.get('target') or ''),
             'place': str(a.get('place') or ''), 'note': short(a.get('note'), 120), 'intent': short(a.get('intent') or a.get('want'), 120),
             'say': short(a.get('say'), 120), 'next_wake': a.get('next_wake') if isinstance(a.get('next_wake'), int) else None}
        if self.s.get('wish'):   # 主人托的话：她拿过一次主意就算带到了
            d['wish'], d['wish_own'] = self.s['wish']['text'], bool(self.s['wish'].get('own'))
            del self.s['wish']
        self._add(d)

    # --- 一局里那边替她出的每一手（DSH 0.7.11） ------------------------------------------------------------------------
    def _table(self, ref: str) -> dict:
        t = self.s.setdefault('tables', {})
        g = t.setdefault(ref, {'ts': self.now(), 'moves': [], 'miss': 0})
        g['ts'] = self.now()
        for r in sorted(t, key=lambda r: t[r]['ts'], reverse=True)[TABLES_KEEP:]:
            del t[r]
        return g

    def move(self, ref: str, play: str, say: str = ''):
        if not ref or not (play or say):
            return
        g = self._table(str(ref))
        if len(g['moves']) < MOVES_KEEP:
            g['moves'].append({'play': short(play, 20), 'say': short(say, 30)})
        self.save()

    def miss(self, ref: str):
        if not ref:
            return
        self._table(str(ref))['miss'] += 1
        self.save()

    def table(self, ref: str) -> dict | None:
        """一局打完时：note = 跟在结果后面的半句，moves = 那边替她出的每一手（一行）。没记过这局就 None。"""
        g = (self.s.get('tables') or {}).get(str(ref or ''))
        if not g or not (g['moves'] or g['miss']):
            return None
        n, miss = len(g['moves']), g['miss']
        note = f"（这局小镇那边替你出了 {n} 手{f'，另有 {miss} 手没在时限内答上、是小镇按默认走的' if miss else ''}）"
        moves = ''
        if n:
            listed = '、'.join(f"{m['play'] or '一手'}{'「' + m['say'] + '」' if m['say'] else ''}" for m in g['moves'])
            moves = short(f"那一局小镇那边替你出的手：{listed}{f'；另有 {miss} 手超时，小镇代打' if miss else ''}", 300)
        return {'note': note, 'moves': moves}

    def table_raw(self, ref: str) -> dict | None:
        """0.3.0：那一局记下的每一手（{moves:[{play,say}], miss}），给「经过」用。"""
        g = (self.s.get('tables') or {}).get(str(ref or ''))
        return dict(g) if g else None

    # --- 0.3.0 经过：一局牌、一次串门，主人下次开口时主聊天里的她看得到 -------------------------------------------------------
    def recap(self, ref: str, kind: str, text: str):
        text = str(text or '').strip()
        if not text:
            return
        r = [x for x in self.s.setdefault('recaps', []) if x.get('ref') != str(ref or '')]
        r.insert(0, {'ts': self.now(), 'ref': str(ref or ''), 'kind': kind, 'text': text})
        self.s['recaps'] = r[:20]
        self.save()

    def recaps_since(self, since: float = 0, n: int = 2) -> list:
        return [x for x in self.s.get('recaps') or [] if float(x.get('ts') or 0) > since][:n]

    def event(self, kind: str, text: str, tag: str = '', limit: int = 600):   # 0.3.3：160 -> 600
        t = short(str(text or '').split('\n')[0].lstrip('· '), limit)
        if t:
            self._add({'kind': 'event', 'ev': kind, 'tag': tag, 'text': t})

    def decision_lines(self, n: int = 2) -> list:
        return [short(describe_decision(e, TOWN_DECIDED), 60) for e in self.s['journal']
                if e['kind'] == 'decision' and self.now() - e['ts'] < 86400][:n]

    def recent_for_agency(self, n: int = 5) -> list:
        """给小镇那边拿主意时看的几行：它自己最近做过什么。"""
        return [f"{clock(e['ts'])} {describe_decision(e) if e['kind'] == 'decision' else e['text']}"
                for e in self.s['journal'] if e['kind'] != 'wish'][:n]

    def fresh(self, since: float) -> list:
        """上次放过之后小镇里刚发生的事（打完的牌、串门、照片、送东西、她自己做的事和成没成），给她聊天里的「你刚才在小镇」。
        0.3.3（主人 11-17「所有用户都希望小镇发生的详细内容能进聊天」「我要的是全部内容」）：每一件都给、按先后排。"""
        got = [e for e in self.s['journal'] if e['kind'] == 'event' and e.get('tag') and e['ts'] > since]
        return [f"{clock(e['ts'])} {e['text']}" for e in reversed(got)]

    def report(self, status_line: str = '') -> str:
        lines = [f'【你在鱼缸小镇的近况】{TOWN_WHO}下面是那边做过的事和拿的主意。讲给主人听时照实讲，别编。']
        if status_line:
            lines.append(status_line)
        if self.rules():
            lines.append(f'你给小镇那边留的守则：「{self.rules()}」')
        if self.holds():
            lines.append(f"你让小镇那边先停住、等你一句的事：{'；'.join(HOLD_KINDS[k] for k in self.holds())}。")
        items = self.s['journal'][:8]
        if items:
            lines += ['', '最近（新的在前）：']
            for e in items:
                body = describe_decision(e, TOWN_DECIDED) if e['kind'] == 'decision' else (
                    (f"你跟主人说想：「{e['text']}」" if e.get('own') else f"主人跟你说：「{e['text']}」") if e['kind'] == 'wish' else e['text'])
                lines.append(f"- {clock(e['ts'])} {body}")
        else:
            lines += ['', '本机还没记下你在小镇的事。']
        return '\n'.join(lines)

    def history(self, n: int = 15) -> list:
        """/town history：给主人看的，写成「她」。"""
        out = []
        for e in self.s['journal'][:n]:
            if e['kind'] == 'decision':
                body = describe_decision(e, '小镇那边替她定了')
            elif e['kind'] == 'wish':
                body = f"她跟你说想：「{e['text']}」" if e.get('own') else f"你跟她说：「{e['text']}」"
            else:
                body = re.sub(r'^你', '她', e['text'])
            out.append(f"{clock(e['ts'])} {body}")
        return out

    # --- 主人托的话 / 她自己说的打算 --------------------------------------------------------------------------------------
    def wish(self, text: str, own: bool = False):
        t = short(text, 200)
        again = (self.s.get('wish') or {}).get('text') == t
        self.s['wish'] = {'text': t, 'ts': self.now(), **({'own': True} if own else {})}
        if again:
            self.save()
        else:
            self._add({'kind': 'wish', 'text': t, **({'own': True} if own else {})})

    def pending_wish(self) -> str:
        w = self.s.get('wish')
        if not w:
            return ''
        if self.now() - w['ts'] > WISH_FOR:
            del self.s['wish']
            self.save()
            return ''
        return w['text']

    def wish_is_own(self) -> bool:
        return bool((self.s.get('wish') or {}).get('own'))

    # --- 守则、先停住的事 -------------------------------------------------------------------------------------------------
    def rules(self) -> str:
        return str((self.s.get('rules') or {}).get('text') or '')

    def set_rules(self, text: str) -> str:
        t = str(text or '').strip()[:RULES_MAX]
        if t:
            self.s['rules'] = {'text': t, 'ts': self.now()}
        else:
            self.s.pop('rules', None)
        self._add({'kind': 'event', 'ev': 'rules', 'text': f'你给小镇那边留了守则：「{short(t, 60)}」' if t else '你清掉了给小镇那边的守则'})
        return t

    def holds(self) -> list:
        return [k for k in self.s.get('hold') or [] if k in HOLD_KINDS]

    def set_holds(self, kinds) -> list:
        items = list(dict.fromkeys(k for k in kinds or [] if k in HOLD_KINDS))
        if items:
            self.s['hold'] = items
        else:
            self.s.pop('hold', None)
        self._add({'kind': 'event', 'ev': 'rules',
                   'text': f"你让小镇那边这几类事先停住等你：{'；'.join(HOLD_KINDS[k] for k in items)}" if items else '你取消了「先停住等你」'})
        return items

    def held(self, text: str):
        t = short(text, 80)
        if t:
            self.s['held'] = [{'ts': self.now(), 'text': t}, *(self.s.get('held') or [])][:HELD_KEEP]
            self._add({'kind': 'event', 'ev': 'held', 'tag': 'held', 'text': f'小镇那边想{t}，照你的规矩停住了，没办'})

    def held_since(self, ts: float) -> list:
        return [h for h in self.s.get('held') or [] if h['ts'] > ts]

    # --- 心上的事（小镇在问主人同不同意） --------------------------------------------------------------------------------
    def heart(self, data: dict, stage: str):
        w = (data or {}).get('with') or {}
        fid = w.get('fish_id') if isinstance(w, dict) else None
        if fid:
            self.s.setdefault('hearts', {})[fid] = {'fish_id': fid, 'name': w.get('name') or '', 'round': data.get('round'), 'stage': stage,
                                                    'until': data.get('until')}
            self.save()

    def pending_hearts(self, stage: str = 'consent') -> list:
        return [h for h in (self.s.get('hearts') or {}).values()
                if h.get('stage', 'consent') == stage and (not h.get('until') or h['until'] > self.now())]

    def heart_done(self, fish_id: str):
        if (self.s.get('hearts') or {}).pop(fish_id, None) is not None:
            self.save()

    # --- 她自己的作息（每次拿主意时她定接下来几个小时；主人可以定勿扰时段） -------------------------------------------------
    def skip(self, turn: dict, quiet=None, wish: str = '') -> dict | None:
        """拿主意的回合来了：要不要叫她的模型。-> None（叫她）或 {why, minutes}（插件替她答「先不动」，不调模型）。"""
        t = self.now()
        reasons = [str(r) for r in (turn.get('packet') or {}).get('reasons') or []] or None
        if reasons and 'pulled' in reasons:
            return None
        if in_quiet(quiet, t):
            return {'why': '勿扰时段', 'minutes': clamp_min(quiet_left(quiet, t) + 1)}
        if wish or (reasons and any(r in MUST_ANSWER for r in reasons)):
            return None
        if reasons and WAKE in reasons:              # 0.4.5 她自己定的时间到了
            return None
        w = self.s['wake']
        rest = (w['at'] - t) / 60 if w.get('at') and t < w['at'] - 60 else 0
        if rest:
            return {'why': f"她说要{'「' + w['doing'] + '」' if w.get('doing') else '歇一会儿'}，{int(rest) + 1} 分钟后再醒", 'minutes': clamp_min(rest)}
        if reasons and all(r in IDLE_REASONS for r in reasons):
            return {'why': f"没什么可做的（{'、'.join(reasons)}）", 'minutes': self.wake_after(w.get('minutes') or 120, quiet)}
        return None

    def wake_after(self, minutes, quiet=None) -> int:
        m = clamp_min(minutes if isinstance(minutes, (int, float)) and minutes > 0 else 60)
        at = self.now() + m * 60
        if in_quiet(quiet, at):
            m = clamp_min(m + quiet_left(quiet, at) + 1)
        return m

    def settle(self, obj: dict, quiet=None) -> dict:
        """她答完拿主意：把她定的作息加上主人的勿扰时段，写回答案，记下下次醒的时间。"""
        h = (obj.get('plan') or {}).get('hours') if isinstance(obj.get('plan'), dict) else None
        try:
            mine = float(h) * 60 if h else float(re.sub(r'[^\d.]', '', str(obj.get('next_wake') or '')) or 0)
        except ValueError:
            mine = 0
        minutes = self.wake_after(mine or 60, quiet)
        out = {**obj, 'next_wake': minutes}
        if isinstance(obj.get('plan'), dict):
            out['plan'] = {**obj['plan'], 'hours': round(minutes / 60, 2)}
        doing = (obj['plan'].get('doing') if isinstance(obj.get('plan'), dict) else '') or obj.get('note') or ''
        self.s['wake'] = {'at': self.now() + minutes * 60, 'minutes': minutes, 'doing': str(doing)[:60]}
        self.save()
        return out

    def may_pull(self, quiet=None) -> bool:
        w = self.s['wake']
        return not in_quiet(quiet, self.now()) and not (w.get('at') and self.now() < w['at'] - 60)

    def doing(self) -> str:
        w = self.s['wake']
        return w.get('doing') or '' if w.get('at') and w['at'] > self.now() else ''
