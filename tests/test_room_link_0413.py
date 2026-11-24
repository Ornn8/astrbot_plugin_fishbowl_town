"""0.4.13 进房间（室内表达力会话 11-24，小镇 feat/1125-room-links；和 DSH test/room-link-0917.test.mjs 同一套）：室内放好了，她拿到一条进屋看的链接
https://island.fishbowltown.com/?in=L-xxxx——确认回执里、先「照片在检查」后「放好了」的那份（问岛上或小镇事件）、do=status、主人私聊的背景备注都带着；
小镇没给就照地号拼。"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town import cobuild as C  # noqa: E402

from test_cobuild import INSIDE, OWNER, FakeTown  # noqa: E402
from test_cobuild import setup as cb_setup  # noqa: E402

LINK = 'https://island.fishbowltown.com/?in=L-0185'


def run(c):
    return asyncio.run(c)


class PlacedTown(FakeTown):
    async def isle_interior_confirm(self, body):
        self.sent.append(('inside_confirm', body))
        return {'ok': True, 'code': 'placed', 'paid_coin': 128, 'link': LINK, 'text': f'放好了 {LINK}'}


async def confirmed(cb, town):
    await cb.start('L-0185', '一个柜台', True, 1)
    await cb.make(INSIDE)
    cb.observe_owner(OWNER, '交吧')
    await cb.submit(OWNER)
    town.inside = [{'iid': 'Iabc123456', 'state': 'quoted', 'coin': 128, 'upkeep_day_coin': 2.4, 'lines': [], 'area_m2': 48, 'rooms': 2}]
    cb.now.t += C.POLL_S + 1
    await cb.tick()
    cb.observe_owner(OWNER, '就这样')
    return await cb.confirm(OWNER)


def notes(j):
    return '\n'.join(t for _, t in j.j)


def test_room_link_and_see_line():
    assert C.ROOM_URL == 'https://island.fishbowltown.com/?in='
    assert C.room_link({'lot': 'L-0107'}, {'link': LINK}) == LINK
    assert C.room_link({'lot': 'L-0107'}) == 'https://island.fishbowltown.com/?in=L-0107'                  # 老小镇没给：照地号拼
    assert C.room_link({'lot': 'L-0107'}, {'link': 'https://evil.example/?in=L-1'}) == 'https://island.fishbowltown.com/?in=L-0107'
    assert C.room_link({'lot': 'B-0009'}) == '' and C.room_link({'lot': 'gift:abc'}) == ''                     # 楼号、送的家不拼
    assert C.room_link({'lot': 'L-0152-2'}) == 'https://island.fishbowltown.com/?in=L-0152-2'               # 一块地第 2 栋起（11-25）
    assert C.room_link({'lot': 'L-0152-2-3'}) == ''
    assert C.room_link({'lot': 'L-0152'}, {'link': 'https://island.fishbowltown.com/?in=L-0152-2'}) == 'https://island.fishbowltown.com/?in=L-0152-2'
    assert C.see_line('') == '' and C.see_line(LINK) == f'进去看：{LINK}（发给主人，点开就进到屋里）'


def test_placed_at_once_receipt_status_and_note_carry_the_link(tmp_path):
    town = PlacedTown()
    cb, _, _, _, j = cb_setup(tmp_path, town=town)

    async def flow():
        c = await confirmed(cb, town)
        assert c['text'] == f'这一层的室内放好了：付了 128 鲸币。上岛以后因为没有室内停业的店自己开门。进去看：{LINK}（发给主人，点开就进到屋里）', c['text']
        st = (await cb.status())['text']
        assert f'1 楼室内放好了。进去看：{LINK}' in st, st
        line = cb.context_line()
        assert line.startswith('你和主人做的 ') and line.endswith(f'放好了，主人想看就把这个链接发给他：{LINK}'), line
        assert LINK in notes(j)
    run(flow())


def test_checking_then_placed_by_polling_no_link_from_an_old_town_built_from_the_lot(tmp_path):
    cb, _, town, _, j = cb_setup(tmp_path)

    async def flow():
        c = await confirmed(cb, town)
        assert '确认了：付了 128 鲸币。这一层的照片在过看图检查' in c['text'] and 'island.fishbowltown' not in c['text']
        assert 'island.fishbowltown' not in cb.context_line()                                               # 还在检查：不给链接
        town.inside = [{'iid': 'Iabc123456', 'state': 'checking', 'coin': 128}]
        cb.now.t += C.POLL_S + 1
        await cb.tick()
        assert '放上岛了' not in notes(j)
        town.inside = [{'iid': 'Iabc123456', 'state': 'placed', 'coin': 128}]                                # 老小镇：没有 link
        cb.now.t += C.POLL_S + 1
        await cb.tick()
        assert f'过了检查，放上岛了。进去看：{LINK}（发给主人，点开就进到屋里）' in notes(j)
        assert f'主人想看就把这个链接发给他：{LINK}' in cb.context_line()
        assert f'1 楼室内放好了。进去看：{LINK}' in (await cb.status())['text']
    run(flow())


def test_checking_then_the_town_event_inside_placed(tmp_path):
    cb, _, town, _, j = cb_setup(tmp_path)

    async def flow():
        await confirmed(cb, town)
        cb.on_event({'kind': 'isle_build', 'data': {'what': 'inside_placed', 'iid': 'Iabc123456', 'lot': 'L-0185', 'link': LINK}})
        assert f'过了检查，放上岛了。进去看：{LINK}' in notes(j)
        assert f'主人想看就把这个链接发给他：{LINK}' in cb.context_line()
    run(flow())


def test_checking_then_refused_she_is_told_and_can_hand_it_in_again(tmp_path):
    cb, _, town, _, j = cb_setup(tmp_path)

    async def flow():
        await confirmed(cb, town)
        town.inside = [{'iid': 'Iabc123456', 'state': 'refused', 'problems': ['照片里有不合适的图']}]
        cb.now.t += C.POLL_S + 1
        await cb.tick()
        assert '没过照片检查，没上岛：照片里有不合适的图' in notes(j)
        assert 'island.fishbowltown' not in cb.context_line()
        await cb.start('L-0185', '', True, 1)                                                               # 接着做同一份草稿
        assert cb.active()['refused']['problems'] == ['照片里有不合适的图'] and cb.active()['placed'] is None
        assert '上次交的室内岛上没收：照片里有不合适的图' in (await cb.status())['text']
    run(flow())
