"""几样只读的小镇数据 -> 给她看（工具）或给主人看（命令）的几行字（DSH 插件 extras.js 的 Python 版）。"""
from __future__ import annotations

import re
import time

BJ = 8 * 3600
VISIT_STATE = {'talking': '还在聊', 'done': '', 'not_home': '对方不在家', 'declined': '对方没开门', 'failed': '没聊成', 'knocking': '在敲门'}


def _arr(x) -> list:
    return x if isinstance(x, list) else []


def _str(v, n: int = 40) -> str:
    s = re.sub(r'\s+', ' ', str(v or '')).strip()
    return s[:n] + '…' if len(s) > n else s


def _nm(x) -> str:
    return _str(x if isinstance(x, str) else (x or {}).get('name') or (x or {}).get('town_name') or '', 16) if x else ''


def _when(ts) -> str:
    try:
        return time.strftime('%m-%d %H:%M', time.gmtime(float(ts) + BJ))
    except (TypeError, ValueError):
        return ''


def album(a: dict | None, name: str = '她') -> str:
    items, making, back = _arr((a or {}).get('items')), _arr((a or {}).get('making')), _arr((a or {}).get('given_back'))
    if not items and not making and not back:
        return f'{name} 的相册还是空的。'
    out = [f'{name} 的相册（{len(items)} 项，新的在前）：']
    for it in items[:12]:
        kind = {'song': '歌', 'paint': '画'}.get(it.get('kind'), '照片')
        who = '、'.join(x for x in (_nm(w) for w in _arr(it.get('with'))) if x)
        paid = f"{_nm(it['paid_by'])}请的" if it.get('paid_by') else ''
        out.append(f"- [{kind}] {_str(it.get('what'), 60)}{'｜和' + who if who else ''}{'｜' + str(it['occasion']) if it.get('occasion') else ''}{'｜' + paid if paid else ''}")
    if len(items) > 12:
        out.append(f'……还有 {len(items) - 12} 项。')
    out += [f"- 正在做：{_str(m.get('what'), 50)}" for m in making[:5]]
    out += [f"- 没做成：{_str(b.get('what'), 40)}{'，' + _str(b.get('reason'), 40) if b.get('reason') else ''}" for b in back[:5]]
    out.append('照片和画在「参观鱼缸小镇」页面找到她以后能看（https://fishbowltown.com/town/tour）。')
    return '\n'.join(out)


def phone(p: dict | None, name: str = '她') -> str:
    if not isinstance(p, dict):
        return '小镇没给小手机的信息。'
    threads = _arr(p.get('threads'))
    out = [f'{name} 的小手机：']
    if not threads:
        out.append('还没有消息。')
    for t in threads[:6]:
        who = '、'.join(_nm(w) for w in _arr(t.get('with'))) or '（群）'
        out.append(f"和{who}{'（' + str(t['unread']) + ' 条还没看）' if t.get('unread') else ''}：")
        for m in _arr(t.get('messages'))[-4:]:
            out.append(f"  {m.get('name') or ''}：{_str(m.get('text'), 60)}{'（没发出去：' + str(m['refused']) + '）' if m.get('refused') else ''}")
    for m in [m for m in _arr(p.get('meets')) if m.get('state') in ('proposed', 'accepted', 'started')][:4]:
        state = {'proposed': '等回话', 'accepted': '说定了', 'started': '见着了'}.get(m['state'], m['state'])
        out.append(f"约见面：{m.get('when_zh') or ''} 在{m.get('place_name') or m.get('place')}，和{m.get('with')}（{state}）")
    return '\n'.join(out)


def _place(v: dict, me: str) -> str:
    if v.get('place') == 'home':
        return f'在{me}家' if v.get('role') == 'host' else f"去{_nm(v.get('with'))}家"
    return f"在{v.get('place_name') or v.get('place') or '镇上'}"


def chats(data: dict | None, name: str = '她', hint: str = '') -> str:
    visits = _arr((data or {}).get('visits'))
    if not visits:
        return f'{name} 还没串过门。'
    out = [f'{name} 最近的串门{hint}：']
    for i, v in enumerate(visits, 1):
        who = f"{_nm(v.get('with'))}来找{name}" if v.get('role') == 'host' else f"{name}去找{_nm(v.get('with'))}"
        state = f"（{VISIT_STATE[v['state']]}）" if VISIT_STATE.get(v.get('state')) else ''
        said = v.get('summary') or '、'.join(_arr(v.get('topics'))) or (f"聊了 {v['lines']} 句" if v.get('lines') else '')
        out.append(f"{i}. {_when(v.get('started'))} {who}，{_place(v, name)}{state}{'：' + _str(said, 60) if said else ''}")
    return '\n'.join(out)


def chat(one: dict | None, name: str = '她', n=None) -> str:
    v = (one or {}).get('visit') or {}
    rows = _arr((one or {}).get('dialogue'))
    who = f"{_nm(v.get('with'))}来找{name}" if v.get('role') == 'host' else f"{name}去找{_nm(v.get('with'))}"
    out = [f"{str(n) + '. ' if n else ''}{_when(v.get('started'))} {who}，{_place(v, name)}{'，聊了 ' + str(v['lines']) + ' 句' if v.get('lines') else ''}"]
    if v.get('summary'):
        out.append(f"（{_str(v['summary'], 80)}）")
    if not rows:
        out.append('这次的话小镇没留下来。' if v.get('lines') else '没聊上。')
    for r in rows:
        if isinstance(r, dict) and r.get('kind') == 'outcome':
            o = r.get('outcome') or {}
            bits = [x for x in (_nm(o.get('from')), o.get('kind'), _str(o.get('text') or o.get('line') or o.get('what') or o.get('item'), 40)) if x]
            if bits:
                out.append(f"（{' · '.join(bits)}）")
        elif isinstance(r, dict) and isinstance(r.get('text'), str):
            out.append(f"{_nm(r) or '？'}：{r['text']}")
    if v.get('ending'):
        out.append(f"结尾：{_str(v['ending'], 80)}")
    return '\n'.join(out)
