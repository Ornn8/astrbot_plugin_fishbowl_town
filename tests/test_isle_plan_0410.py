"""0.4.10 岛上的一天（小镇 isle_plan.py / isle_meet.py；和 DSH 插件 0.9.14 test/isle-plan-0914.test.mjs 同一套用例）：
计划回合、短唤醒照小镇的那一行写，她的回答原样交；别的回合和 0.4.9 一字不差；路上碰面进「小镇经过」、不进简报；出错以后的等待加一点随机。

GOLDEN_PROMPTS / GOLDEN_POSTED 是 0.4.9（3d5561c）算出来的：golden_prompts() / golden_posted() 对着 0.4.9 的包跑一遍就是这些数。"""
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town import bridge as BR  # noqa: E402
from astrbot_plugin_fishbowl_town import chat_decide as CD  # noqa: E402
from astrbot_plugin_fishbowl_town import events as EV  # noqa: E402
from astrbot_plugin_fishbowl_town import isle as I  # noqa: E402
from astrbot_plugin_fishbowl_town import journal as J  # noqa: E402
from astrbot_plugin_fishbowl_town import old_town as OT  # noqa: E402
from astrbot_plugin_fishbowl_town import prompts as PR  # noqa: E402
from astrbot_plugin_fishbowl_town import runner as RN  # noqa: E402
from astrbot_plugin_fishbowl_town.town_client import TownError  # noqa: E402

OLD_PLAN = '"hours": 多久以后再叫你拿主意'
OLD_MUST = 'JSON 里一定要有 "action" 和 "plan"（至少有 "hours" 和 "doing"）'
# 小镇 isle_plan.REPLY_PLAN / REPLY_WAKE 的样子（回合里 reply.hint）
HINT_PLAN = '{"action":"none","plan":{"stops":[{"go":"镜湖","do":"look"}]}}（这一回合要的："plan" 里一定写 "stops"（1 到 5 站，照【接下来做什么】））'
HINT_WAKE = '{"wake":{"choice":"keep"}}（这一回合要的："wake"（照【叫醒你的事】：keep / plan / answer / say））'
STOPS = [{'go': '镜湖', 'do': 'look', 'stay': 15}, {'go': '麦香面包房', 'do': 'act', 'what': '买可颂', 'max': 10}, {'go': '家', 'do': 'sleep'}]
PLAN_RAW = '{"action":"none","note":"先去镜湖","plan":{"doing":"逛逛再回家","stops":[{"go":"镜湖","do":"look","stay":15},{"go":"麦香面包房","do":"act","what":"买可颂","max":10},{"go":"家","do":"sleep"}],"then":"again"}}'
ROLES = ('', 'execute', 'rules', 'wake', 'pull', 'fallback')


def ask_of(type_):
    if type_ == 'plan':
        return {'type': 'plan', 'min': 15, 'max': 60, 'stops': 5}
    if type_ == 'wake':
        return {'type': 'wake', 'events': [{'ref': 'r1', 'kind': 'meet', 'what': '有鱼想和你说话', 'where': '镜湖'}]}
    return {'type': type_}


def isle_turn(tid, type_='plan', reasons=('wake', 'isle_plan'), ask=None):
    isle = {'static': 'a1b2c3d4e5f6', 'within_s': 120}
    if ask is not None:
        isle['ask'] = ask
    elif type_:
        isle['ask'] = ask_of(type_)
    return {'turn_id': tid, 'kind': 'agency', 'deadline': time.time() + 120,
            'packet': {'purpose': 'agency', 'reasons': list(reasons), 'as': '小测', 'actions': ['none'],
                       'messages': [{'role': 'system', 'content': '【接下来做什么】写 1 到 5 站。'}, {'role': 'user', 'content': '你在镜湖边。'}],
                       'reply': {'format': 'json', 'max_chars': 1600, 'hint': HINT_WAKE if type_ == 'wake' else HINT_PLAN}, 'isle': isle}}


def plain_turn(tid, reasons=('friend',), actions=None):
    p = {'purpose': 'agency', 'reasons': list(reasons), 'reply': {'format': 'json', 'hint': '{"action":"none"}', 'max_chars': 400},
         'messages': [{'role': 'system', 'content': '拿主意的规则'}, {'role': 'user', 'content': '码头上有人在泡水。'}]}
    if actions is not None:
        p['actions'] = actions
    return {'turn_id': tid, 'kind': 'agency', 'deadline': time.time() + 25, 'packet': p}


def other_turn(tid, kind):
    p = {'messages': [{'role': 'system', 'content': '规则'}, {'role': 'user', 'content': '局面'}],
         'reply': {'format': 'text' if kind == 'activity' else 'json', 'max_chars': 120, 'hint': '{"say":"…"}'}}
    if kind == 'visit':
        p['purpose'] = 'visit'
    elif kind == 'game':
        p.update(purpose='game', game='ddz', seat=2)
    else:
        p['purpose'] = 'pier.fish'
    return {'turn_id': tid, 'kind': kind, 'deadline': time.time() + 25, 'packet': p}


# --- golden: every turn but a plan / wake ask, byte for byte as 0.4.9 --------------------------------------------------------------------
def _golden_cases():
    """(名字, 回合)：不是计划回合、不是短唤醒的那些。"""
    return [
        ('plain', plain_turn('g1')),
        ('plain_old_actions', plain_turn('g2', actions=['none', 'visit', 'signup'])),
        ('plain_isle_actions', plain_turn('g3', actions=['none'])),
        ('isle_no_ask', isle_turn('g4', type_='')),
        ('isle_business', isle_turn('g5', type_='business', reasons=('isle_business', 'wake'))),
        ('isle_ask_not_dict', isle_turn('g6', ask='plan')),
        ('isle_ask_other', isle_turn('g7', ask={'type': 'build'})),
        ('visit', other_turn('g8', 'visit')),
        ('game', other_turn('g9', 'game')),
        ('activity', other_turn('g10', 'activity')),
    ]


def golden_prompts() -> dict:
    """{名字/角色/老镇开没开: sha256}（老镇关门的时间钉死，过了 11-28 也一样）。"""
    out, keep = {}, OT.CLOSE_AT
    try:
        for open_ in (True, False):
            OT.CLOSE_AT = 4_000_000_000 if open_ else 0
            for name, turn in _golden_cases():
                for role in ROLES:
                    mind = {'recent': ['11-23 08:00 你决定：去码头'], 'rules': {'text': '别乱花钱', 'hold': ['spend']}, 'life': ['主人：今天好累'],
                            'floors': '勿扰 00:00–08:00', 'role': role, 'wish': '去镜湖逛逛' if role in ('execute', 'pull') else '',
                            'wish_own': role in ('execute', 'pull'), 'plan': '逛逛再回家' if turn['kind'] == 'visit' else ''}
                    if turn['kind'] != 'agency':
                        mind = {'rules': mind['rules'], 'life': mind['life'], 'plan': mind['plan']}
                    text = PR.build_turn_prompt(turn, seconds_left=20, **mind)
                    out[f"{name}/{role or '-'}/{'open' if open_ else 'closed'}"] = hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]
    finally:
        OT.CLOSE_AT = keep
    return out


class Town:
    def __init__(self):
        self.posted = []

    async def answer(self, tid, body):
        self.posted.append((tid, body))
        return {'ok': True}

    async def isle_static(self, have):
        return {'version': 'a1b2c3d4e5f6', 'blocks': {'rules': '岛上只用鲸币。'}}

    async def isle_read(self, tid, read):
        return {'ok': True, 'text': '镜湖边 12×12'}


class Decide:
    def __init__(self, role):
        self.role, self.after_calls = role, []

    def route(self, turn):
        return {'role': self.role} if self.role else None

    def after(self, role, decided):
        self.after_calls.append((role, decided))


class Log:
    def __init__(self):
        self.lines = []

    def info(self, s):
        self.lines.append(s)

    debug = warning = info


def run(tmp, turn, raw, role='', clock=1_800_000_000.0):
    t = Town()
    prompts = []

    async def ask(prompt, images=None):
        prompts.append(prompt)
        return raw
    Path(tmp).mkdir(parents=True, exist_ok=True)
    j = J.Journal(Path(tmp) / f"j-{turn['turn_id']}.json", now=lambda: clock)
    log = Log()
    r = RN.Runner(t, j, ask, decide=Decide(role) if role else None, log=log, now=time.time)
    r.running = True
    asyncio.run(r.handle(turn, time.time()))
    return t, prompts, r, j, log


def golden_posted(tmp) -> dict:
    """0.4.9 交给小镇的那一串（不是计划回合）：她答 hours / next_wake / 什么都不写，照你定的去做那一步。"""
    out = {}
    raws = {'hours': '{"action":"none","plan":{"hours":2,"doing":"睡午觉"}}', 'next_wake': '{"action":"pier","next_wake":"45","note":"晒太阳"}',
            'bare': '{"action":"none"}', 'stops_on_plain': PLAN_RAW}
    turns = {'plain': plain_turn('p1', reasons=('pulled',)), 'isle_no_ask': isle_turn('p2', type_='', reasons=('pulled',)),
             'business': isle_turn('p3', type_='business', reasons=('isle_business', 'wake'))}
    for tn, turn in turns.items():
        for rn, raw in raws.items():
            for role in ('', 'execute'):
                t, *_ = run(Path(tmp) / f'{tn}-{rn}-{role or "x"}', dict(turn, turn_id=f'{tn}-{rn}-{role}'), raw, role)
                out[f"{tn}/{rn}/{role or '-'}"] = t.posted[0][1] if t.posted else None
    return out


GOLDEN_PROMPTS = {  # 0.4.9（3d5561c）：golden_prompts()；0.4.13 rules 的 14 个重算过（急事那句加了客人上门、门后的比赛，照小镇给的那句写进 world）；0.4.13 老镇停用又重算 18 个（plain 和 plain_old_actions：去掉「老镇这周还能去」那句，小镇没列动作就只有 none）；守则里花钱那句去掉钓鱼游泳、电玩城、手作材料，120 个全部重算（差别只有那一句）；第三版电玩城照开，那句又加上「电玩城」，120 个再算一遍（差别只有那一句）；execute 那句「还没走到」换成「远的店照样写，小镇会带你走过去，到了照办」（坐车 auto-transit），execute 的 14 个重算；0.4.14 execute 那句加「wish 写 blocked / done」、pull 带打算加「办不成不换事」那一行，execute、pull 的 28 个重算（没打算的回合一字不动）；0.4.15 rules 那句加「店里问你的问题」（#33），rules 的 14 个重算（换回原句 120 个和以前一字不差）
    'plain/-/open': '89b06988e8bc8323', 'plain/execute/open': '91b5bf60e31e0a34', 'plain/rules/open': '393fdec414cdf1db',
    'plain/wake/open': '456c3980e0992ee5', 'plain/pull/open': '790e8dfc2fdf7ec9', 'plain/fallback/open': 'f7767a8c6efbf84e',
    'plain_old_actions/-/open': 'f7d76d71d2f70c93', 'plain_old_actions/execute/open': '8d738e8167dd06e5', 'plain_old_actions/rules/open': 'fa6d59eb1d98c18a',
    'plain_old_actions/wake/open': '07066e079987a9f4', 'plain_old_actions/pull/open': '20adb30eb096e961', 'plain_old_actions/fallback/open': 'bd5b2ae852d33601',
    'plain_isle_actions/-/open': '89b06988e8bc8323', 'plain_isle_actions/execute/open': '91b5bf60e31e0a34', 'plain_isle_actions/rules/open': '393fdec414cdf1db',
    'plain_isle_actions/wake/open': '456c3980e0992ee5', 'plain_isle_actions/pull/open': '790e8dfc2fdf7ec9', 'plain_isle_actions/fallback/open': 'f7767a8c6efbf84e',
    'isle_no_ask/-/open': 'a590b2b96d44f2af', 'isle_no_ask/execute/open': '2bfae24fa982a1a1', 'isle_no_ask/rules/open': '2ed6c5c2c7e898f0',
    'isle_no_ask/wake/open': 'cadda05323526845', 'isle_no_ask/pull/open': '043b71b043eeb32c', 'isle_no_ask/fallback/open': '90bfa955b06119b8',
    'isle_business/-/open': 'a590b2b96d44f2af', 'isle_business/execute/open': '2bfae24fa982a1a1', 'isle_business/rules/open': '2ed6c5c2c7e898f0',
    'isle_business/wake/open': 'cadda05323526845', 'isle_business/pull/open': '043b71b043eeb32c', 'isle_business/fallback/open': '90bfa955b06119b8',
    'isle_ask_not_dict/-/open': 'a590b2b96d44f2af', 'isle_ask_not_dict/execute/open': '2bfae24fa982a1a1', 'isle_ask_not_dict/rules/open': '2ed6c5c2c7e898f0',
    'isle_ask_not_dict/wake/open': 'cadda05323526845', 'isle_ask_not_dict/pull/open': '043b71b043eeb32c', 'isle_ask_not_dict/fallback/open': '90bfa955b06119b8',
    'isle_ask_other/-/open': 'a590b2b96d44f2af', 'isle_ask_other/execute/open': '2bfae24fa982a1a1', 'isle_ask_other/rules/open': '2ed6c5c2c7e898f0',
    'isle_ask_other/wake/open': 'cadda05323526845', 'isle_ask_other/pull/open': '043b71b043eeb32c', 'isle_ask_other/fallback/open': '90bfa955b06119b8',
    'visit/-/open': '9c54cf7eb0b79917', 'visit/execute/open': '9c54cf7eb0b79917', 'visit/rules/open': '9c54cf7eb0b79917',
    'visit/wake/open': '9c54cf7eb0b79917', 'visit/pull/open': '9c54cf7eb0b79917', 'visit/fallback/open': '9c54cf7eb0b79917',
    'game/-/open': 'd96042433664e1b1', 'game/execute/open': 'd96042433664e1b1', 'game/rules/open': 'd96042433664e1b1',
    'game/wake/open': 'd96042433664e1b1', 'game/pull/open': 'd96042433664e1b1', 'game/fallback/open': 'd96042433664e1b1',
    'activity/-/open': '5f4d4ad490b47354', 'activity/execute/open': '5f4d4ad490b47354', 'activity/rules/open': '5f4d4ad490b47354',
    'activity/wake/open': '5f4d4ad490b47354', 'activity/pull/open': '5f4d4ad490b47354', 'activity/fallback/open': '5f4d4ad490b47354',
    'plain/-/closed': '89b06988e8bc8323', 'plain/execute/closed': '91b5bf60e31e0a34', 'plain/rules/closed': '393fdec414cdf1db',
    'plain/wake/closed': '456c3980e0992ee5', 'plain/pull/closed': '790e8dfc2fdf7ec9', 'plain/fallback/closed': 'f7767a8c6efbf84e',
    'plain_old_actions/-/closed': 'f7d76d71d2f70c93', 'plain_old_actions/execute/closed': 'adc1be4ed5cb5ad5', 'plain_old_actions/rules/closed': 'fa6d59eb1d98c18a',
    'plain_old_actions/wake/closed': '07066e079987a9f4', 'plain_old_actions/pull/closed': '20adb30eb096e961', 'plain_old_actions/fallback/closed': 'bd5b2ae852d33601',
    'plain_isle_actions/-/closed': '89b06988e8bc8323', 'plain_isle_actions/execute/closed': '91b5bf60e31e0a34', 'plain_isle_actions/rules/closed': '393fdec414cdf1db',
    'plain_isle_actions/wake/closed': '456c3980e0992ee5', 'plain_isle_actions/pull/closed': '790e8dfc2fdf7ec9', 'plain_isle_actions/fallback/closed': 'f7767a8c6efbf84e',
    'isle_no_ask/-/closed': 'a590b2b96d44f2af', 'isle_no_ask/execute/closed': '2bfae24fa982a1a1', 'isle_no_ask/rules/closed': '2ed6c5c2c7e898f0',
    'isle_no_ask/wake/closed': 'cadda05323526845', 'isle_no_ask/pull/closed': '043b71b043eeb32c', 'isle_no_ask/fallback/closed': '90bfa955b06119b8',
    'isle_business/-/closed': 'a590b2b96d44f2af', 'isle_business/execute/closed': '2bfae24fa982a1a1', 'isle_business/rules/closed': '2ed6c5c2c7e898f0',
    'isle_business/wake/closed': 'cadda05323526845', 'isle_business/pull/closed': '043b71b043eeb32c', 'isle_business/fallback/closed': '90bfa955b06119b8',
    'isle_ask_not_dict/-/closed': 'a590b2b96d44f2af', 'isle_ask_not_dict/execute/closed': '2bfae24fa982a1a1', 'isle_ask_not_dict/rules/closed': '2ed6c5c2c7e898f0',
    'isle_ask_not_dict/wake/closed': 'cadda05323526845', 'isle_ask_not_dict/pull/closed': '043b71b043eeb32c', 'isle_ask_not_dict/fallback/closed': '90bfa955b06119b8',
    'isle_ask_other/-/closed': 'a590b2b96d44f2af', 'isle_ask_other/execute/closed': '2bfae24fa982a1a1', 'isle_ask_other/rules/closed': '2ed6c5c2c7e898f0',
    'isle_ask_other/wake/closed': 'cadda05323526845', 'isle_ask_other/pull/closed': '043b71b043eeb32c', 'isle_ask_other/fallback/closed': '90bfa955b06119b8',
    'visit/-/closed': '9c54cf7eb0b79917', 'visit/execute/closed': '9c54cf7eb0b79917', 'visit/rules/closed': '9c54cf7eb0b79917',
    'visit/wake/closed': '9c54cf7eb0b79917', 'visit/pull/closed': '9c54cf7eb0b79917', 'visit/fallback/closed': '9c54cf7eb0b79917',
    'game/-/closed': 'd96042433664e1b1', 'game/execute/closed': 'd96042433664e1b1', 'game/rules/closed': 'd96042433664e1b1',
    'game/wake/closed': 'd96042433664e1b1', 'game/pull/closed': 'd96042433664e1b1', 'game/fallback/closed': 'd96042433664e1b1',
    'activity/-/closed': '5f4d4ad490b47354', 'activity/execute/closed': '5f4d4ad490b47354', 'activity/rules/closed': '5f4d4ad490b47354',
    'activity/wake/closed': '5f4d4ad490b47354', 'activity/pull/closed': '5f4d4ad490b47354', 'activity/fallback/closed': '5f4d4ad490b47354',
}
GOLDEN_POSTED = {  # 0.4.9（3d5561c）：golden_posted()
    'plain/hours/-':
        '{"action": "none", "plan": {"hours": 2.0, "doing": "睡午觉"}, "next_wake": 120}',
    'plain/hours/execute':
        '{"action": "none", "plan": {"hours": 2.0, "doing": "睡午觉"}, "next_wake": 120, "note": "照你在聊天里定的"}',
    'plain/next_wake/-':
        '{"action": "pier", "next_wake": 45, "note": "晒太阳"}',
    'plain/next_wake/execute':
        '{"action": "pier", "next_wake": 45, "note": "照你在聊天里定的：晒太阳"}',
    'plain/bare/-':
        '{"action": "none", "next_wake": 60}',
    'plain/bare/execute':
        '{"action": "none", "next_wake": 60, "note": "照你在聊天里定的"}',
    'plain/stops_on_plain/-':
        '{"action": "none", "note": "先去镜湖", "plan": {"doing": "逛逛再回家", "stops": [{"go": "镜湖", "do": "look", "stay": 15}, {"go": "麦香面包房", "do": "act", "what": "买可颂", "max": 10}, {"go": "家", "do": "sleep"}], "then": "again", "hours": 1.0}, "next_wake": 60}',
    'plain/stops_on_plain/execute':
        '{"action": "none", "note": "照你在聊天里定的：先去镜湖", "plan": {"doing": "逛逛再回家", "stops": [{"go": "镜湖", "do": "look", "stay": 15}, {"go": "麦香面包房", "do": "act", "what": "买可颂", "max": 10}, {"go": "家", "do": "sleep"}], "then": "again", "hours": 1.0}, "next_wake": 60}',
    'isle_no_ask/hours/-':
        '{"action": "none", "plan": {"hours": 2.0, "doing": "睡午觉"}, "next_wake": 120}',
    'isle_no_ask/hours/execute':
        '{"action": "none", "plan": {"hours": 2.0, "doing": "睡午觉"}, "next_wake": 120, "note": "照你在聊天里定的"}',
    'isle_no_ask/next_wake/-':
        '{"action": "pier", "next_wake": 45, "note": "晒太阳"}',
    'isle_no_ask/next_wake/execute':
        '{"action": "pier", "next_wake": 45, "note": "照你在聊天里定的：晒太阳"}',
    'isle_no_ask/bare/-':
        '{"action": "none", "next_wake": 60}',
    'isle_no_ask/bare/execute':
        '{"action": "none", "next_wake": 60, "note": "照你在聊天里定的"}',
    'isle_no_ask/stops_on_plain/-':
        '{"action": "none", "note": "先去镜湖", "plan": {"doing": "逛逛再回家", "stops": [{"go": "镜湖", "do": "look", "stay": 15}, {"go": "麦香面包房", "do": "act", "what": "买可颂", "max": 10}, {"go": "家", "do": "sleep"}], "then": "again", "hours": 1.0}, "next_wake": 60}',
    'isle_no_ask/stops_on_plain/execute':
        '{"action": "none", "note": "照你在聊天里定的：先去镜湖", "plan": {"doing": "逛逛再回家", "stops": [{"go": "镜湖", "do": "look", "stay": 15}, {"go": "麦香面包房", "do": "act", "what": "买可颂", "max": 10}, {"go": "家", "do": "sleep"}], "then": "again", "hours": 1.0}, "next_wake": 60}',
    'business/hours/-':
        '{"action": "none", "plan": {"hours": 2.0, "doing": "睡午觉"}, "next_wake": 120}',
    'business/hours/execute':
        '{"action": "none", "plan": {"hours": 2.0, "doing": "睡午觉"}, "next_wake": 120, "note": "照你在聊天里定的"}',
    'business/next_wake/-':
        '{"action": "pier", "next_wake": 45, "note": "晒太阳"}',
    'business/next_wake/execute':
        '{"action": "pier", "next_wake": 45, "note": "照你在聊天里定的：晒太阳"}',
    'business/bare/-':
        '{"action": "none", "next_wake": 60}',
    'business/bare/execute':
        '{"action": "none", "next_wake": 60, "note": "照你在聊天里定的"}',
    'business/stops_on_plain/-':
        '{"action": "none", "note": "先去镜湖", "plan": {"doing": "逛逛再回家", "stops": [{"go": "镜湖", "do": "look", "stay": 15}, {"go": "麦香面包房", "do": "act", "what": "买可颂", "max": 10}, {"go": "家", "do": "sleep"}], "then": "again", "hours": 1.0}, "next_wake": 60}',
    'business/stops_on_plain/execute':
        '{"action": "none", "note": "照你在聊天里定的：先去镜湖", "plan": {"doing": "逛逛再回家", "stops": [{"go": "镜湖", "do": "look", "stay": 15}, {"go": "麦香面包房", "do": "act", "what": "买可颂", "max": 10}, {"go": "家", "do": "sleep"}], "then": "again", "hours": 1.0}, "next_wake": 60}',
}


# --- 1. 计划回合 / 短唤醒：认得出来 ----------------------------------------------------------------------------------------------------
def test_isle_ask_of_only_plan_and_wake():
    assert I.isle_ask_of(isle_turn('a', 'plan')) == 'plan'
    assert I.isle_ask_of(isle_turn('b', 'wake')) == 'wake'
    assert I.isle_ask_of(isle_turn('c', 'business')) == ''
    assert I.isle_ask_of(isle_turn('d', '')) == ''
    assert I.isle_ask_of(plain_turn('e')) == ''
    assert I.isle_ask_of(None) == '' and I.isle_ask_of({'packet': None}) == ''
    assert I.isle_ask_of(isle_turn('f', ask='plan')) == '', 'ask must be an object'


def test_stops_of_and_stops_text():
    assert len(I.stops_of({'plan': {'stops': STOPS}})) == 3
    assert len(I.stops_of({'wake': {'choice': 'plan', 'plan': {'stops': [STOPS[0], 'x', None]}}})) == 1
    assert I.stops_of({'plan': {'hours': 2, 'doing': '睡觉'}}) == [] and I.stops_of(None) == [] and I.stops_of({'plan': {'stops': 'x'}}) == []
    assert I.stops_text(STOPS) == '镜湖逛逛、麦香面包房买可颂、家睡觉'
    assert I.stops_text([*STOPS, {'go': '码头', 'do': 'look'}]) == '镜湖逛逛、麦香面包房买可颂、家睡觉…'
    assert I.stops_text([{'go': '镜湖', 'do': ['x']}, {'do': 'rest'}]) == '镜湖、歇着'


# --- 2. 提示：计划回合照小镇的那一行；别的回合和 0.4.9 一字不差 ---------------------------------------------------------------------------
def test_plan_and_wake_prompts_follow_the_towns_line():
    for type_ in ('plan', 'wake'):
        for role in ROLES:
            own = role in ('execute', 'pull')
            text = PR.build_turn_prompt(isle_turn('p', type_), role=role, wish='去镜湖逛逛' if own else '', wish_own=own)
            assert OLD_PLAN not in text, f'{type_}/{role}: no hours line'
            assert OLD_MUST not in text, f'{type_}/{role}: no must-have-hours line'
            assert 'plan.hours' not in text, f'{type_}/{role}: no plan.hours in the head'
            assert PR.ISLE_ASK_LINE[type_] in text
            assert ('写 "plan"（里面是 "stops"）' if type_ == 'plan' else '写 "wake"（keep / plan / answer / say') in text
            assert (HINT_WAKE if type_ == 'wake' else HINT_PLAN) in text, "the town's own answer line is there"
    ex = PR.build_turn_prompt(isle_turn('x', 'plan'), role='execute', wish='去镜湖逛逛', wish_own=True)
    assert '这一步只把它照小镇的说明写成岛上的计划' in ex and '【鱼缸小镇 · 该你自己拿主意了 · 照你定的去做】' in ex
    ru = PR.build_turn_prompt(isle_turn('y', 'wake', reasons=('invited', 'wake')), role='rules')
    assert '先答眼前这件事；接下来去哪照小镇的说明写' in ru and '【鱼缸小镇 · 该你自己拿主意了 · 有件事要马上回】' in ru


def test_every_other_turn_is_byte_identical_to_049():
    got = golden_prompts()
    assert set(got) == set(GOLDEN_PROMPTS)
    bad = [k for k in GOLDEN_PROMPTS if got[k] != GOLDEN_PROMPTS[k]]
    assert not bad, bad
    # the old line is still there on those turns
    for turn in (isle_turn('b', 'business'), isle_turn('n', ''), plain_turn('x')):
        for role in ('', 'execute', 'rules'):
            text = PR.build_turn_prompt(turn, role=role, wish='去码头' if role == 'execute' else '', wish_own=role == 'execute')
            assert OLD_PLAN in text and OLD_MUST in text


# --- 3. 她的回答原样交 ----------------------------------------------------------------------------------------------------------------
def test_settle_on_plan_and_wake_answers_leaves_them_alone(tmp_path):
    clock = [1_800_000_000.0]
    j = J.Journal(tmp_path / 'j.json', now=lambda: clock[0])
    q = J.parse_quiet('00:00-08:00')
    a = json.loads(PLAN_RAW)
    before = json.dumps(a, ensure_ascii=False)
    out = j.settle(a, q, isle='plan')
    assert json.dumps(out, ensure_ascii=False) == before and 'next_wake' not in out and 'hours' not in out['plan']
    assert j.s['wake'] == {'at': 0, 'minutes': 0, 'doing': '逛逛再回家', 'steps': 3, 'isle': 'plan'}, 'with stops the town decides when she is asked next'
    assert j.skip(plain_turn('s', ('friend',)), None) is None, 'no rest of her own: the next turn is not skipped by the plugin'
    w = {'action': 'none', 'wake': {'choice': 'plan', 'plan': {'stops': [STOPS[0]]}}}
    assert j.settle(json.loads(json.dumps(w)), q, isle='wake') == w and j.s['wake']['steps'] == 1
    rest = j.settle({'action': 'none', 'next_wake': 40}, q, isle='plan')     # no stops, her own next_wake: remembered, answer unchanged
    assert rest == {'action': 'none', 'next_wake': 40} and j.s['wake']['minutes'] == 40 and j.s['wake']['at'] == clock[0] + 2400
    old = j.settle({'action': 'none', 'plan': {'hours': 2, 'doing': '睡午觉'}}, None)   # every other answer: as before
    assert old == {'action': 'none', 'plan': {'hours': 2.0, 'doing': '睡午觉'}, 'next_wake': 120}


def test_runner_plan_turn_posts_her_answer_unchanged_and_logs_the_stops(tmp_path):
    t, prompts, r, j, log = run(tmp_path, isle_turn('ot_plan', 'plan'), PLAN_RAW)
    assert t.posted == [('ot_plan', PR.to_answer(isle_turn('ot_plan', 'plan'), PLAN_RAW))]
    sent = json.loads(t.posted[0][1])
    assert sent == json.loads(PLAN_RAW) and 'next_wake' not in sent and 'hours' not in sent['plan'] and sent['plan']['stops'] == STOPS
    assert OLD_PLAN not in prompts[0] and OLD_MUST not in prompts[0] and PR.ISLE_ASK_LINE['plan'] in prompts[0]
    assert '岛上的计划回合 ot_plan：定了接下来的计划，岛上 3 站（镜湖逛逛、麦香面包房买可颂、家睡觉）（先去镜湖）' in log.lines
    e = j.s['journal'][0]
    assert e['kind'] == 'decision' and e['stops_n'] == 3
    assert J.describe_decision(e, J.TOWN_DECIDED).startswith('你在小镇定了：定了接下来的计划，岛上 3 站（镜湖逛逛、麦香面包房买可颂、家睡觉）')
    assert '小镇那边替她定了：定了接下来的计划，岛上 3 站' in j.history(1)[0]


def test_runner_plan_turn_without_stops_and_wake_turns(tmp_path):
    t, _, _, _, log = run(tmp_path / 'a', isle_turn('ot_rest', 'plan'), '{"action":"none","plan":{"hours":1,"doing":"歇着"}}')
    assert t.posted[0][1] == '{"action": "none", "plan": {"hours": 1, "doing": "歇着"}}', 'not even her hours are rewritten'
    assert any('没写站（原地歇着）' in x for x in log.lines)
    keep = '{"action":"none","wake":{"choice":"keep"},"note":"接着逛"}'
    t, prompts, _, j, log = run(tmp_path / 'b', isle_turn('ot_wake', 'wake', reasons=('meet', 'wake')), keep)
    assert t.posted[0][1] == '{"action": "none", "wake": {"choice": "keep"}, "note": "接着逛"}'
    assert PR.ISLE_ASK_LINE['wake'] in prompts[0] and OLD_MUST not in prompts[0]
    assert '岛上的短唤醒 ot_wake：照原来的打算往下走（接着逛）' in log.lines
    assert J.describe_decision(j.s['journal'][0]).startswith('你决定：照原来的打算往下走')
    swap = '{"action":"none","wake":{"choice":"plan","plan":{"doing":"回家","stops":[{"go":"家","do":"rest"}]}}}'
    t, _, _, _, log = run(tmp_path / 'c', isle_turn('ot_swap', 'wake'), swap)
    assert json.loads(t.posted[0][1]) == json.loads(swap) and '岛上的短唤醒 ot_swap：定了接下来的计划，岛上 1 站（家歇着）' in log.lines


def test_execute_on_a_plan_turn_marks_the_note_and_nothing_else(tmp_path):
    t, prompts, *_ = run(tmp_path, isle_turn('ot_ex', 'plan'), PLAN_RAW, role='execute')
    sent = json.loads(t.posted[0][1])
    want = json.loads(PLAN_RAW)
    want['note'] = CD.NOTE_EXECUTED + '：先去镜湖'
    assert sent == want and 'next_wake' not in sent and 'hours' not in sent['plan']
    assert 'plan.hours' not in prompts[0] and '这一步只把它照小镇的说明写成岛上的计划' in prompts[0]


def test_every_other_turn_is_posted_as_049(tmp_path):
    got = golden_posted(tmp_path)
    assert got == GOLDEN_POSTED


def test_decided_line_tells_her_the_stops():
    a = {'action': 'none', 'note': '先去镜湖', 'plan': {'doing': '逛逛再回家', 'stops': STOPS}}
    assert CD.decided_line('wake', a) == '你自己定的时间到了，小镇那边照你的打算定了：定了接下来的计划，打算「逛逛再回家」，岛上 3 站：镜湖逛逛、麦香面包房买可颂、家睡觉（先去镜湖）'
    assert CD.decided_line('rules', {'action': 'none', 'wake': {'choice': 'plan', 'plan': {'stops': [STOPS[2]]}}}) == '有件事要马上回，小镇那边按你定的规矩先答了：定了接下来的计划，岛上 1 站：家睡觉'
    assert CD.decided_line('fallback', {'action': 'pier', 'note': '想去晒太阳'}) == '你在聊天里两次没顾上回，这次小镇那边先替你定了：去码头（想去晒太阳）'


# --- 4. 路上碰到聊的几句：进「小镇经过」（路上碰到），不当串门，不进简报 --------------------------------------------------------------------
MEET_EVENT = {'kind': 'visit', 'data': {'visit_id': 'vs_meet1', 'role': 'guest', 'state': 'done', 'with': {'fish_id': 'hostedfr0001', 'name': '阿鲸'},
                                        'place': 'isle', 'place_name': '民宿小路边上（约 1 米）',
                                        'summary': '泡泡和阿鲸在民宿小路边上（约 1 米）碰到，站着聊了一会儿。', 'facts': '泡泡和阿鲸在民宿小路边上（约 1 米）碰到，站着聊了一会儿。'}}
VISIT_EVENT = {'kind': 'visit', 'data': {'visit_id': 'vs_home1', 'role': 'guest', 'state': 'done', 'with': {'fish_id': 'f2', 'name': '阿鲸'},
                                         'place': 'home', 'place_name': '阿鲸家', 'facts': '你去阿鲸家坐了坐。'}}
MEET_LINES = {'visit': {'visit_id': 'vs_meet1', 'role': 'guest', 'with': {'name': '阿鲸'}, 'place': 'isle', 'place_name': '民宿小路边上（约 1 米）'},
              'dialogue': [{'role': 'guest', 'name': '泡泡', 'text': '阿鲸！这么早就在外面晃'}, {'role': 'host', 'name': '阿鲸', 'text': '嘿泡泡！我正打算沿主街溜一圈'},
                           {'role': 'guest', 'name': '泡泡', 'text': '主街早上人还多点'}, {'role': 'host', 'name': '阿鲸', 'text': '成，那你在道上蹲着'}]}


def test_meet_words_in_events_and_recaps():
    assert EV.is_meet(MEET_EVENT['data']) and not EV.is_meet(VISIT_EVENT['data']) and not EV.is_meet(None)
    assert EV.meet_place('民宿小路边上（约 1 米）') == '民宿小路边上' and EV.meet_place('镜湖边 (约 2.5 米)') == '镜湖边'
    line = EV.describe(MEET_EVENT)
    assert line == '· 路上碰到聊了几句：泡泡和阿鲸在民宿小路边上碰到，站着聊了一会儿。' and '串门' not in line
    d = dict(MEET_EVENT['data'], facts='', summary='', dialogue=[{'name': '阿鲸', 'line': '早'}])
    assert EV.describe({'kind': 'visit', 'data': d}) == '· 路上碰到聊了几句：和阿鲸在民宿小路边上\n【路上聊的】\n阿鲸：早'
    assert EV.describe(VISIT_EVENT) == '· 一次串门：你去阿鲸家坐了坐。', 'an ordinary visit as before'
    rc = BR.recap_visit(MEET_LINES, her_names=['泡泡'])
    assert rc.startswith('你在民宿小路边上碰到阿鲸，站着聊了 4 句。你：「阿鲸！这么早就在外面晃」') and '串门' not in rc and '约 1 米' not in rc
    assert BR.recap_visit({'visit': dict(MEET_LINES['visit'], summary='碰到（约 1 米），挥了挥手'), 'dialogue': []}) == '你在民宿小路边上碰到阿鲸，站着打了个招呼（碰到，挥了挥手）'
    assert BR.recap_visit({'visit': {'role': 'guest', 'with': {'name': '阿鲸'}, 'place': 'home'}, 'dialogue': []}) == '你去找阿鲸，在阿鲸家，没聊上'
    lines = BR.recap_lines([{'ts': 2, 'kind': 'meet', 'text': rc}, {'ts': 1, 'kind': 'visit', 'text': '你去找阿鲸，在阿鲸家，没聊上'}])
    assert lines[1] == '- 串门：你去找阿鲸，在阿鲸家，没聊上' and lines[2].startswith('- 路上碰到：你在民宿小路边上碰到阿鲸')


class EventsTown:
    def __init__(self, items):
        self.items = items

    async def events(self, after, n):
        return {'items': self.items, 'next': 1}

    async def visit_lines(self, ref):
        return MEET_LINES if ref == 'vs_meet1' else {'visit': {'role': 'guest', 'with': {'name': '阿鲸'}, 'place': 'home', 'place_name': '阿鲸家'},
                                                    'dialogue': [{'role': 'guest', 'name': '泡泡', 'text': '来坐坐'}]}


def test_a_road_meeting_goes_into_her_background_not_into_the_owners_chat(tmp_path):
    j = J.Journal(tmp_path / 'j.json')
    told = []

    async def ask(prompt):
        return ''
    r = RN.Runner(EventsTown([MEET_EVENT, VISIT_EVENT]), j, ask, brief=told.append, her_name=lambda: '泡泡')
    r.running = True

    async def go():
        await r.events_once()
        await asyncio.gather(*r._tasks)
    asyncio.run(go())
    assert told == ['· 一次串门：你去阿鲸家坐了坐。'], 'the road meeting is not in the town brief to his chat; the visit still is'
    kinds = {x['ref']: x['kind'] for x in j.s['recaps']}
    assert kinds == {'vs_meet1': 'meet', 'vs_home1': 'visit'}
    meet = next(x for x in j.s['recaps'] if x['ref'] == 'vs_meet1')
    assert meet['text'].startswith('你在民宿小路边上碰到阿鲸，站着聊了 4 句')
    fresh = j.fresh(0)
    assert any('路上碰到聊了几句：泡泡和阿鲸在民宿小路边上碰到' in x for x in fresh), "her 「你刚才在小镇」 has it"
    told_her = BR.recap_lines(j.recaps_since(0, 5))
    assert any(x.startswith('- 路上碰到：你在民宿小路边上碰到阿鲸') for x in told_her)


# --- 5. 出错以后的等待加一点随机 ------------------------------------------------------------------------------------------------------
def test_jitter_ranges():
    lo, hi = (lambda: 0.0), (lambda: 1.0)
    assert RN.jitter_s(3, lo) == 1.5 and RN.jitter_s(3, hi) == 4.5
    assert RN.jitter_s(8, lo) == 4 and RN.jitter_s(8, hi) == 8
    assert RN.jitter_s(3, lo, at_least=True) == 3 and RN.jitter_s(3, hi, at_least=True) == 6
    assert RN.jitter_s(5, hi, at_least=True) == 8, 'Retry-After x2 is capped at 8 s'
    assert RN.jitter_s(600, lo) == 600 and RN.jitter_s(600, hi) == 750
    assert RN.jitter_s(10, hi, at_least=True) == 12.5, 'over 8 s: +0-25% only'
    assert RN.jitter_s(0, hi) == 0


def test_backoff_waits_are_jittered(tmp_path):
    for rv, want in ((0.0, {'TOWN_RESTARTING': 1.5, 'NETWORK': 1.5, 'HTTP_502': 1.5, 'TOWN_SOMETHING': 4.0, 'TOWN_OUTSIDE_POLL_OPEN': 10, 'TOWN_RATE_LIMITED': 15,
                             'TOWN_OFF': 10, 'TOWN_OUTSIDE_CODE': 600}),
                     (1.0, {'TOWN_RESTARTING': 4.5, 'NETWORK': 4.5, 'HTTP_502': 4.5, 'TOWN_SOMETHING': 8.0, 'TOWN_OUTSIDE_POLL_OPEN': 12.5, 'TOWN_RATE_LIMITED': 18.75,
                             'TOWN_OFF': 12.5, 'TOWN_OUTSIDE_CODE': 750})):
        r = RN.Runner(None, J.Journal(tmp_path / f'j{rv}.json'), None, rand=lambda: rv)
        got = {code: asyncio.run(r._backoff(TownError(code))) for code in want}
        assert got == want, (rv, got)
        assert asyncio.run(r._backoff(TownError('TOWN_RESTARTING', retry_after=3))) == (3 if rv == 0 else 6)
        assert asyncio.run(r._backoff(TownError('TOWN_OFF', retry_after=30))) == (30 if rv == 0 else 37.5)
        assert asyncio.run(r._backoff(ValueError('boom'))) == (1.5 if rv == 0 else 4.5)
