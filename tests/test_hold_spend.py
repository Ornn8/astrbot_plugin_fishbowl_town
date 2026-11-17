"""0.3.1（和 DSH 0.8.3 test/hold-spend.test.mjs 同一套用例）：spend 也拦码头、电玩城、手作、想做的事和计划步骤里的小钱；座位费、收手作不拦。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town.prompts import HOLD_KINDS, hold_check  # noqa: E402


def test_spend_hold_takes_out_the_small_piggy_spends_and_keeps_seat_fees_and_harvest():
    real = {'action': 'none', 'want': '去码头边坐着看水，不游泳不钓鱼，就待着', 'note': '下水要花两分，先记着，等她自己来。', 'plan': {'hours': 2, 'steps': []}}
    found, clean = hold_check('agency', json.dumps(real, ensure_ascii=False), ['spend'])
    assert 'want' not in json.loads(clean) and '想做的事要花钱' in ''.join(found)
    found, clean = hold_check('agency', json.dumps({'action': 'pier', 'pier': {'do': 'fish', 'casts': 2}}), ['spend'])
    assert json.loads(clean) == {'action': 'none'} and found == ['去码头钓鱼']
    assert json.loads(hold_check('agency', json.dumps({'do': 'fish', 'casts': 1}), ['spend'])[1]) == {}
    assert json.loads(hold_check('agency', json.dumps({'action': 'arcade', 'arcade': {'do': 'play', 'machine': 'claw'}}), ['spend'])[1])['action'] == 'none'
    plan = {'action': 'none', 'plan': {'hours': 3, 'steps': [{'in_minutes': 10, 'do': 'pier'}, {'in_minutes': 40, 'do': 'signup', 'game': 'ddz'}, {'in_minutes': 90, 'do': 'arcade'}]}}
    found, clean = hold_check('agency', json.dumps(plan), ['spend'])
    assert json.loads(clean)['plan']['steps'] == [{'in_minutes': 40, 'do': 'signup', 'game': 'ddz'}] and found == ['计划里到点去码头', '计划里到点去电玩城']
    for free in ({'action': 'signup', 'game': 'ddz'}, {'action': 'make', 'make': {'do': 'harvest', 'run_id': 'r1'}},
                 {'action': 'pier', 'pier': {'do': 'bottle', 'text': '你好'}}, {'action': 'none', 'want': '去广场转转'}):
        assert hold_check('agency', json.dumps(free, ensure_ascii=False), ['spend'])[0] == [], free
    assert hold_check('agency', json.dumps({'action': 'pier', 'pier': {'do': 'fish'}}), ['promise'])[0] == []
    assert '钓鱼' in HOLD_KINDS['spend'] and '座位费不算' in HOLD_KINDS['spend']
