"""0.4.13 守店来客叫得醒插件（keeper-survival 11-24：线上 20 家守店店约 430 件客人全作废——她说要歇着时插件替她答了「先不动」，
MUST_ANSWER、URGENT 里都没有 guest / door；她有自己的打算时 execute 又排在 URGENT 前面）。和 DSH 0.9.17 test/keeper-wake-0917.test.mjs 同一套用例。"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town.chat_decide import PLAN_MARK, ChatDecide, decided_line  # noqa: E402
from astrbot_plugin_fishbowl_town.journal import Journal, parse_quiet  # noqa: E402
from astrbot_plugin_fishbowl_town.prompts import build_turn_prompt  # noqa: E402


def agency(tid, reasons):
    return {'turn_id': tid, 'kind': 'agency', 'deadline': time.time() + 25,
            'packet': {'purpose': 'agency', 'reasons': list(reasons), 'reply': {'format': 'json', 'hint': '{"action":"none"}', 'max_chars': 400},
                       'messages': [{'role': 'system', 'content': '拿主意的规则'}, {'role': 'user', 'content': '【等你回】客人 #12 押了 6 鲸币想买海菜饭团'}]}}


def test_a_guest_or_a_door_game_wakes_her_while_she_rests_but_not_in_the_owner_quiet_hours(tmp_path):
    now = 1_800_000_000.0
    j = Journal(tmp_path / 'j.json', now=lambda: now)
    j.s['wake'] = {'at': now + 3 * 3600, 'minutes': 180, 'doing': '睡觉', 'lastAsk': now - 60}
    turn = lambda reasons: {'packet': {'reasons': reasons}}  # noqa: E731
    assert j.skip(turn(['table'])), 'an ordinary reason still lets her rest'
    assert j.skip(turn(['guest'])) is None, 'a guest is waiting with a deposit: her model is called'
    assert j.skip(turn(['door', 'idle'])) is None, 'her turn in a door game: her model is called'
    m = int(((now + 8 * 3600) % 86400) // 60)                                   # 北京时间这一分钟
    q = parse_quiet(f'{m // 60:02d}:00-{(m // 60 + 1) % 24:02d}:59')
    assert j.skip(turn(['guest']), q)['why'] == '勿扰时段', 'the owner quiet hours are his floor'


def test_a_guest_or_a_door_game_is_answered_by_her_rules_before_her_own_plan(tmp_path):
    j = Journal(tmp_path / 'j.json', now=lambda: 1_800_000_000.0)

    async def ask_main(text):
        return f'嗯\n{PLAN_MARK}去码头'
    d = ChatDecide(j, ask_main=ask_main, home_umo=lambda: 'qq:FriendMessage:1', idle_s=lambda: 600, config={}, now=lambda: 1_800_000_000.0)
    j.wish('去码头泡一会儿', own=True)
    assert d.route(agency('g1', ['guest'])) == {'role': 'rules'}
    assert d.route(agency('g2', ['door', 'wake'])) == {'role': 'rules'}
    assert j.pending_wish() == '去码头泡一会儿'
    assert d.route(agency('g3', ['wake'])) == {'role': 'execute'}


def test_the_rules_prompt_says_to_copy_the_line_the_town_gave_into_world():
    p = build_turn_prompt(agency('p1', ['guest']), seconds_left=60, role='rules')
    assert '客人上门、门后的比赛轮到你、别的鱼邀请你' in p
    assert '客人上门、门后的比赛轮到你、店里问你的问题：照小镇在「等你回」里给的那一句写进 "world"（照抄，只改数目和你要说的话），action 填 none。' in p


def test_after_the_rules_role_served_a_guest_her_chat_self_reads_that_she_served_him():
    reply = {'action': 'none', 'world': {'do': 'reply', 'event': 1, 'actions': [{'op': 'take', 'thing': 'coin', 'n': 10}, {'op': 'say', 'text': '小柒来啦，茶刚沏好。'}]},
             'plan': {'hours': 1, 'doing': '守着茶摊'}}
    head = '有件事要马上回，小镇那边按你定的规矩先答了：'
    assert decided_line('rules', reply) == head + '接待了客人（说「小柒来啦，茶刚沏好。」），打算「守着茶摊」'
    assert decided_line('rules', {'action': 'none', 'world': {'do': 'door_step', 'run': 'r1', 'choice': 'a'}}) == head + '在门后的比赛里走了一步'
    assert decided_line('rules', {'action': 'none', 'world': {'do': 'reply', 'event': 2, 'actions': [{'op': 'say', 'text': '…'}]}}) == head + '接待了客人'
    assert decided_line('rules', {'action': 'none', 'world': {'do': 'go', 'place': '码头'}}) == head + '先不动', 'other world steps: as before'


def test_a_keeper_who_wrote_action_world_and_put_her_words_in_result_say_still_reads_that_she_served_him():
    """线上店主 11-24 的 3 单：action 写 world，话写在 world.result.say / result.text 里。"""
    head = '有件事要马上回，小镇那边按你定的规矩先答了：'
    sell = {'action': 'world', 'world': {'do': 'reply', 'event': 499, 'actions': [{'kind': 'sell', 'good': 'candle', 'n': 1, 'price': 6}],
                                          'result': {'say': '一支香薰蜡烛，六鲸币…'}}}
    assert decided_line('rules', sell) == head + '接待了客人（说「一支香薰蜡烛，六鲸币…」）'
    serve = {'action': 'world', 'world': {'do': 'reply', 'event': 7, 'actions': [{'do': 'serve', 'good': '蛋炒饭', 'n': 1}], 'result': {'text': '蛋炒饭好啦'}}}
    assert decided_line('rules', serve) == head + '接待了客人（说「蛋炒饭好啦」）', 'result.text too'
    both = {'action': 'world', 'world': {'do': 'reply', 'event': 8, 'actions': [{'op': 'say', 'text': '麦冬，面来了'}], 'result': {'say': '别的话'}}}
    assert decided_line('rules', both) == head + '接待了客人（说「麦冬，面来了」）', 'op:say wins over result'
    assert decided_line('rules', {'action': 'world', 'world': {'do': 'reply', 'event': 9, 'actions': [], 'result': {'say': '…'}}}) == head + '接待了客人'
    assert decided_line('rules', {'action': 'world', 'world': {'do': 'door_step', 'run': 'r1', 'choice': 'a'}}) == head + '在门后的比赛里走了一步'
    visit = {'action': 'visit', 'target': '小柒', 'world': {'do': 'reply', 'event': 9, 'result': {'say': '来了'}}}
    assert decided_line('rules', visit) == head + '去找小柒串门', 'a real action keeps its own words'


def test_0415_a_reply_with_q_answers_a_shops_question_not_serving_a_guest():
    """0.4.15（小镇 11-26 #33）：带 q 的 reply 是她当客人回店里的问题，聊天里的她读到的是这个，不是「接待了客人」。"""
    head = '有件事要马上回，小镇那边按你定的规矩先答了：'
    assert decided_line('rules', {'action': 'none', 'world': {'do': 'reply', 'q': 'q_69e9e77d91', 'value': {'go': True}}}) == head + '回了店里问你的问题'
    assert decided_line('rules', {'action': 'none', 'world': {'do': 'replies', 'replies': [{'q': 'q_69e9e77d91', 'value': {'go': True}}]}}) == head + '回了店里问你的问题'
    keeper = {'action': 'none', 'world': {'do': 'reply', 'event': 3, 'q': 'x', 'actions': [{'op': 'say', 'text': '来啦'}]}}
    assert decided_line('rules', keeper) == head + '接待了客人（说「来啦」）', 'event wins: a keeper reply'
