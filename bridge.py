"""0.3.0「两边是同一个她」（DSH 插件 0.8.2 lib/bridge.js 的 Python 版，规则和测试用例相同；方案见 DSH 仓库 docs/MEMORY-BRIDGE-0.8.2.md）：
- 主聊天 -> 小镇：她和主人最近的几句（AstrBot 自己的对话记录）先过滤（scrub_life），只给她自己心里知道；
- 回声检查（echo_check）：她在小镇说出去的话，和她心里那几句比字，连着 6 个汉字一样、或同一串 4 位以上数字，就算说漏了；
- 小镇 -> 主聊天：一次串门、一局牌的经过（recap_visit / recap_game），由代码整理，不调模型。
这里只有纯函数。"""
from __future__ import annotations

import json
import re
import time

ECHO_RUN = 6          # 连着几个汉字一样算说漏
DIGITS_RUN = 4        # 几位以上的数字一样算说漏
LIFE_MAX = 400        # 带进小镇的主聊天内容一共多少字
LIFE_LINES = 4
LIFE_DAYS = 3
VISIT_RECAP_MAX = 300
GAME_RECAP_MAX = 150
ECHO_CORRECTION = ('刚才的回答里带出了你和主人之间的事（他说过的话、你们的私事），镇上别的鱼都看得见。换个说法重答：'
                   '那些事只放在你心里，不要说出来、不要写进任何字段；别的照旧，只输出和刚才同样格式的回答。')

_CJK = re.compile(r'[㐀-鿿豈-﫿]')
_HEAD = re.compile(r'^[^：:]{1,16}[：:]')
NOTE_MARK = '【鱼缸小镇插件'   # 插件附在主人消息后面的背景备注从这里开始，不算主人说的话


def _str(v, n: int) -> str:
    s = re.sub(r'\s+', ' ', str(v if v is not None else '')).strip()
    return s[:n - 1] + '…' if len(s) > n else s


def _text_of(content) -> str:
    """对话记录里一条消息的文字：字符串，或 [{type:text,text}] 里的第一段（后面几段是插件附的备注）。"""
    if isinstance(content, str):
        s = content
    elif isinstance(content, list):
        s = next((str(p.get('text') or '') for p in content if isinstance(p, dict) and p.get('type') == 'text' and str(p.get('text') or '').strip()), '')
    else:
        s = ''
    for mark in (NOTE_MARK, '<system_reminder>', '<system_notice>'):   # 插件的备注、AstrBot 自己附的提醒：都不是主人说的
        i = s.find(mark)
        if i >= 0:
            s = s[:i]
    return s.strip()


def life_from_history(history, *, updated_at: float = 0, now: float | None = None, n: int = LIFE_LINES, per: int = 80) -> list:
    """AstrBot 对话记录（history：JSON 字符串或消息列表）-> 最近 n 行「主人：……」「你：……」。对话 3 天没动过就不带。"""
    now = time.time() if now is None else now
    if updated_at and now - float(updated_at) > LIFE_DAYS * 86400:
        return []
    try:
        msgs = json.loads(history) if isinstance(history, str) else list(history or [])
    except (ValueError, TypeError):
        return []
    out = []
    for m in msgs if isinstance(msgs, list) else []:
        if not isinstance(m, dict) or m.get('role') not in ('user', 'assistant'):
            continue
        t = _text_of(m.get('content'))
        if not t or t.startswith('/'):      # 命令不算
            continue
        out.append(f"{'主人' if m['role'] == 'user' else '你'}：{_str(t, per)}")
    return out[-n:]


def scrub_life(lines, *, max_chars: int = LIFE_MAX) -> list:
    """进小镇之前：去掉网址、邮箱、@谁、4 位以上的数字串，再控制总长。行的样子不变（「主人：……」）。"""
    out, used = [], 0
    for raw in lines if isinstance(lines, (list, tuple)) else []:
        s = str(raw if raw is not None else '')
        m = _HEAD.match(s)
        prefix = m.group(0) if m else ''
        body = s[len(prefix):]
        body = re.sub(r'https?://\S+', '（链接）', body, flags=re.I)
        body = re.sub(r'[\w.+-]+@[\w-]+\.[\w.-]+', '（邮箱）', body)
        body = re.sub(r'[@＠][^\s，。！？、,.!?]{1,20}', '（某人）', body)
        body = re.sub(r'\d[\d\s-]{2,}\d', lambda x: '（一串数字）' if len(re.sub(r'\D', '', x.group(0))) >= DIGITS_RUN else x.group(0), body).strip()
        if not body:
            continue
        s = prefix + body
        if used + len(s) > max_chars:
            if not out:
                out.append(_str(s, max_chars))
            break
        out.append(s)
        used += len(s)
    return out


def _hanzi(text) -> str:
    return ''.join(ch for ch in str(text if text is not None else '') if _CJK.match(ch))


def echo_index(private_lines, *, allow: str = '') -> dict:
    """私密内容里所有连着 ECHO_RUN 个汉字的片段 + 4 位以上的数字串。allow：这一轮小镇材料里本来就有的字，不算说漏。"""
    runs, digits = set(), set()
    allowed = _hanzi(allow)
    for line in private_lines if isinstance(private_lines, (list, tuple)) else []:
        body = _HEAD.sub('', str(line if line is not None else ''), count=1)
        h = _hanzi(body)
        for i in range(len(h) - ECHO_RUN + 1):
            w = h[i:i + ECHO_RUN]
            if w not in allowed:
                runs.add(w)
        for d in re.findall(r'\d{4,}', body):
            if d not in str(allow):
                digits.add(d)
    return {'runs': runs, 'digits': digits, 'empty': not runs and not digits}


def text_hits(text, index) -> list:
    if not index or index.get('empty'):
        return []
    found = []
    h = _hanzi(text)
    for i in range(len(h) - ECHO_RUN + 1):
        w = h[i:i + ECHO_RUN]
        if w in index['runs'] and w not in found:
            found.append(w)
    for d in re.findall(r'\d{4,}', str(text if text is not None else '')):
        if d in index['digits'] and d not in found:
            found.append(d)
    return found


def echo_check(answer, index, *, fmt: str = 'json') -> tuple[list, object]:
    """(撞上的片段, 去掉那几句以后的回答)。fmt='text'：撞上就整句不说（''）；JSON：撞上的字符串字段换成 ''，别的（出的牌、投的票）照旧。"""
    if answer is None or not index or index.get('empty'):
        return [], answer
    if fmt == 'text':
        hits = text_hits(answer, index)
        return hits, ('' if hits else answer)
    try:
        obj = json.loads(answer)
    except (ValueError, TypeError):
        return text_hits(answer, index), answer
    hits: list = []

    def walk(v):
        if isinstance(v, str):
            h = text_hits(v, index)
            if h:
                hits.extend(x for x in h if x not in hits)
                return ''
            return v
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        return v

    cleaned = walk(obj)
    return (hits, json.dumps(cleaned, ensure_ascii=False)) if hits else ([], answer)


def _who(r) -> str:
    return _str((r or {}).get('name') or (r or {}).get('fish_name') or (r or {}).get('speaker') or (r or {}).get('fish_id') or '', 14)


def recap_visit(one, *, her_names=(), max_chars: int = VISIT_RECAP_MAX) -> str:
    """一次串门的经过（主聊天里的她看）：谁来找谁、在哪、她说的话优先，开头一句和最后两句留着，加上结尾，不超过 max_chars 字。
    one = 小镇 visits/<id> 的回答 {visit, dialogue}；her_names = 她在小镇用过的名字（旧小镇没有 role 时认哪几句是她说的）。"""
    v = (one or {}).get('visit') or {}
    rows = [r for r in (one or {}).get('dialogue') or [] if isinstance(r, dict) and isinstance(r.get('text'), str) and r['text'].strip()]
    names = {str(x) for x in her_names or () if x}

    def mine(r):
        return r.get('role') == v.get('role') if v.get('role') and r.get('role') else str(r.get('name') or r.get('fish_name') or '') in names

    w = v.get('with')
    other = _str(w if isinstance(w, str) else (w or {}).get('name') or (w or {}).get('fish_id') or '', 14) or '那条鱼'
    place = (('在你家' if v.get('role') == 'host' else f'在{other}家') if v.get('place') == 'home'
             else f"在{v.get('place_name') or v.get('place') or '镇上'}")
    head = f"{f'{other}来找你' if v.get('role') == 'host' else f'你去找{other}'}，{place}{f'，聊了 {len(rows)} 句' if rows else ''}"
    if not rows:
        return _str(f"{head}{'（' + str(v['summary']) + '）' if v.get('summary') else '，没聊上'}", max_chars)
    keep = {i for i in (0, len(rows) - 1, len(rows) - 2) if i >= 0}
    keep |= {i for i, r in enumerate(rows) if mine(r)}
    picked = sorted(keep)

    def said(r):
        return f"{'你' if mine(r) else _who(r) or '对方'}：「{_str(r['text'], 40)}」"

    tail = f"；结尾：{_str(v['ending'], 40)}" if v.get('ending') else ''

    def build():
        return f"{head}。{' '.join(said(rows[i]) for i in picked)}{tail}"

    while len(build()) > max_chars and len(picked) > 2:
        drop = next((k for k, i in enumerate(picked) if 0 < k < len(picked) - 2 and not mine(rows[i])), -1)
        picked.pop(drop if drop >= 0 else 1)
    return _str(build(), max_chars)


def recap_game(g, *, max_chars: int = GAME_RECAP_MAX) -> str:
    """一局牌的经过：哪一局、结果、她出的几手和说的话，不超过 max_chars 字。"""
    if not isinstance(g, dict):
        return ''
    moves = [m for m in g.get('moves') or [] if isinstance(m, dict)]
    says = [m['say'] for m in moves if m.get('say')]
    plays = [m['play'] for m in moves if m.get('play')]
    parts = [f"{g.get('title') or '一局'}{'：' + str(g['result']) if g.get('result') else ''}"]
    if plays:
        parts.append(f"你出了 {len(plays)} 手（{'、'.join(plays[-3:])}{'……' if len(plays) > 3 else ''}）")
    if says:
        parts.append('你说过：' + ''.join(f'「{s}」' for s in says[-2:]))
    return _str('；'.join(parts), max_chars)


def recap_lines(recaps, since: float = 0) -> list:
    """主聊天的「你刚才在小镇」后面跟的经过：最近两件。"""
    items = [r for r in recaps or [] if isinstance(r, dict) and r.get('text') and float(r.get('ts') or 0) > since][:2]
    if not items:
        return []
    zh = {'visit': '串门', 'game': '牌局'}
    return ['经过（你刚才在小镇的原话和结果）：', *[f"- {zh.get(r.get('kind'), '小镇')}：{_str(r['text'], 320)}" for r in items]]
