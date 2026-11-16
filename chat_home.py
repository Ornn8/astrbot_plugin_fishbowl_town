"""她的「家」：主人和她的私聊（owner_umo）。0.2.0（主人 11-17：不少用户把小镇的会话和自己聊天的分开，说小镇里的是「分身」）。

AstrBot 认得出私聊（FriendMessage）和管理员（is_admin = 主人），所以比 DSH 版简单：
  - 还没绑定：主人第一次在私聊里跟她说话，那个私聊就是她的家（自动认的）；
  - 自动认的家，主人 2 天没在那儿说话、却在另一个私聊里 2 天内说了 3 句以上：家搬过去（最多 12 小时一次）；
  - 主人自己 /town bind，或者她在主人说了以后用 fishbowl_town_here 搬的：手动的，自动纠正不再动它。
群聊、别人的私聊都不算。这里只记时间和聊天的 id，不记内容。
"""
from __future__ import annotations

import time

TWO_DAYS = 48 * 3600
MOVE_GAP = 12 * 3600
MIN_SAID = 3
KEEP = 30


class ChatHome:
    def __init__(self, state: dict, save=lambda: None, now=time.time):
        self.s, self.save, self.now = state, save, now
        self.s.setdefault('owner_said', {})          # umo -> [ts]（主人在私聊里说话的时间）

    def said(self, umo: str) -> None:
        if not umo:
            return
        seen = [t for t in self.s['owner_said'].get(umo, []) if self.now() - t < TWO_DAYS][-KEEP:]
        seen.append(self.now())
        self.s['owner_said'][umo] = seen
        for k in [k for k, v in self.s['owner_said'].items() if not v or self.now() - v[-1] > TWO_DAYS * 2]:
            del self.s['owner_said'][k]
        self.save()

    def count(self, umo: str, window: float = TWO_DAYS) -> int:
        return sum(1 for t in self.s['owner_said'].get(umo, []) if self.now() - t < window)

    def last_said(self, umo: str) -> float:
        seen = self.s['owner_said'].get(umo) or []
        return seen[-1] if seen else 0.0

    def decide(self, current: str, how: str, umo: str) -> str | None:
        """主人刚在私聊 ``umo`` 里说了话：她的家该换成哪个聊天（None = 不换）。``how``：现在的家是 auto / manual / ''。"""
        if not umo:
            return None
        if not current:
            return umo                                                   # 还没绑定：就是这里
        if how == 'manual' or umo == current:
            return None
        if self.now() - float(self.s.get('moved_at') or 0) < MOVE_GAP:
            return None
        if self.count(current) == 0 and self.count(umo) >= MIN_SAID:
            return umo
        return None

    def moved(self, frm: str, to: str) -> None:
        self.s['moved_at'] = self.now()
        self.s['moved'] = {'from': frm, 'to': to, 'at': self.now()}
        self.save()
