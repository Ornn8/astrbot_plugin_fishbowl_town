"""0.4.13 私家车（private-car 会话 11-24，小镇 feat/1125-private-cars，11-25 包，开关默认关；和 DSH test/private-car-0917.test.mjs 同一套）：
私聊里「在岛上做一件事」车行登记车型 {"do":"build","what":"车型",…}、先上牌 {"do":"build","car":"车名","plate":true} 不再被当成盖楼挡回去；
别的 build 照旧挡。自己下牌 {"do":"demolish","car":"车名"} 日记里写「给车下牌」，不写「拆楼」。"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town import isle_tools as T  # noqa: E402


class Journal:
    def __init__(self):
        self.j = []

    def event(self, kind, text, tag='', limit=600):
        self.j.append(text)


class Client:
    def __init__(self):
        self.acts = []

    async def isle_act(self, world, ref=''):
        self.acts.append(world)
        return {'ok': True, 'code': 'done', 'text': '好了'}


async def no_sleep(_s):
    return None


def act(c, j, w):
    return asyncio.run(T.act(c, json.dumps(w, ensure_ascii=False), journal=j, sleep=no_sleep))


def test_is_car_build_and_car_what():
    assert T.is_car_build({'do': 'build', 'what': '车型', 'spot': '店', 'name': '小黄', 'speed': 5, 'seats': 2})
    assert T.is_car_build({'do': 'build', 'what': ' Car '})
    assert T.is_car_build({'do': 'build', 'car': '小黄', 'plate': True})
    assert not T.is_car_build({'do': 'build', 'lot': 'L-0107', 'idea': '海边小屋'})
    assert not T.is_car_build({'do': 'build_guide', 'what': '车型'})
    assert not T.is_car_build({'do': 'demolish', 'car': '小黄'})
    assert T.car_what({'do': 'build', 'what': '车型'}) == '在车行登记车型'
    assert T.car_what({'do': 'build', 'car': '小黄', 'plate': True}) == '给车上牌'
    assert T.car_what({'do': 'demolish', 'car': '小黄'}) == '给车下牌'
    assert T.car_what({'do': 'demolish', 'lot': 'B-0009'}) == ''
    assert T.car_what({'do': 'walk', 'to': '车行', 'how': '开车'}) == ''


def test_car_type_and_plate_go_to_the_island_a_house_build_still_points_to_cobuild():
    c, j = Client(), Journal()
    car_type = {'do': 'build', 'what': '车型', 'spot': '店', 'name': '小黄', 'speed': 5, 'seats': 2}
    assert '「好了」\n办成了' in act(c, j, car_type)
    assert '办成了' in act(c, j, {'do': 'build', 'car': '小黄', 'plate': True})
    assert c.acts == [car_type, {'do': 'build', 'car': '小黄', 'plate': True}]
    assert act(c, j, {'do': 'build', 'lot': 'L-0107', 'idea': '海边小屋'}) == T.BUILD_HERE
    assert act(c, j, {'do': 'confirm_build', 'car': '小黄'}) == T.BUILD_HERE
    assert len(c.acts) == 2
    assert '在岛上在车行登记车型：好了' in j.j[0]
    assert '在岛上给车上牌' in j.j[1]


def test_her_car_off_the_plate_reads_xiapai_a_building_is_still_chailou():
    c, j = Client(), Journal()
    act(c, j, {'do': 'demolish', 'car': '小黄'})
    act(c, j, {'do': 'demolish', 'lot': 'B-0009'})
    assert '给车下牌' in j.j[0] and '拆楼' not in j.j[0]
    assert '拆楼' in j.j[1]
