"""0.4.5 主聊天里的鱼缸岛（11-22 林蓝的主人：「我的鱼获取不到 他现在调用不了岛上的工具 只能用镇上的」；小镇 platform/接口-主聊天岛上工具.md；
DSH 插件 lib/isle-tools.js 的 Python 版）。岛上的事原来只能在小镇派来的回合里写 {"world":{…}}，查看也要开着的回合号：
主聊天里的她什么都看不到、做不了。小镇开了两个不等回合的口子，main.py 在主人的私聊里给她两个工具：
    fishbowl_isle_look  看岛上（POST isle/look {read}）：和回合里查的一样，默认「我的」（她自己的全部，一页）
    fishbowl_isle_act   在岛上做一件事（POST isle/act {world, ref}）：和回合里写的 world 一模一样；岛上回的那句话原样给她
两个口子除了接入码不对、小镇没开外来的鱼、小镇太忙，都回 200 {ok, code, text}：text 原样给她。
旧小镇没有这两个口子（404）：照实说还没开，指回「去小镇」（把打算带进回合里办），不报错。
花主人给的饭票画整套立绘（sprite_set）：要主人在这段聊天里叫她做或点了头，owner_words 填他的原话，没有就不发。
换鲸币（fx_buy）她自己定（0.4.12，主人 11-23「换钱我建议主动点，本来充钱就是给小鱼花的」）：不用主人先开口，也不劝他充值。
盖楼不在这里（小镇回 cobuild）：插件先接住，指到「一起盖楼」（0.4.7 fishbowl_town_build：和主人一起，用 Blender 建，cobuild.py）。
看图：AstrBot 的工具结果只带字（图递不到她的模型），地图、样子这两页只给她图上画的是什么。"""
from __future__ import annotations

import asyncio
import json
import secrets
import time

from .isle import NO_PICS_HEAD, CIRCLED, pics_of
from .town_client import TownError
from .journal import eta_of
from .gallery import GALLERY_NO, GALLERY_YES, UPLOAD_MAX, image_type, is_owner_pic_ref

LOOK_TOPICS = ('我的', '地', '楼', '铺位', '店', '玩的', '订单', '缺的', '统计', '镇规', '怎么做', '我的店', '价', '地图', '样子', '田', '镇民')   # 0.4.9：镇民（小镇 11-24 四层镇民）；0.4.11：玩的（小镇 11-23 play.py）
# 小镇的 TOPICS 还有「住宿」：11-23 线上查它只回「按房间住宿、出租还没开」，等岛上开了再加
OWNER_MONEY = ('sprite_set',)                   # 要他在聊天里叫她做或点了头的（画立绘）；换鲸币 0.4.12 起她自己定
COBUILD = ('build', 'build_guide', 'confirm_build')
# 0.4.13 私家车（小镇 11-25 feat/1125-private-cars，开关默认关）：车行登记车型 {"do":"build","what":"车型",…}、先上牌 {"do":"build","car":"车名","plate":true}
# 也写 do=build，但不是盖楼、不带 lot，放过去交给岛上；别的 build 照旧指去「一起盖楼」。自己下牌是 {"do":"demolish","car":"车名"}。
CAR_WHAT = ('车型', '车', 'car', 'vehicle', '造车')


def is_car_build(w) -> bool:
    return isinstance(w, dict) and w.get('do') == 'build' and (w.get('car') is not None or str(w.get('what') or '').strip().lower() in CAR_WHAT)


def car_what(w) -> str:
    """车的那几件给主人看、记日记的中文名（不是车就是 ''）。"""
    if not isinstance(w, dict) or (w.get('car') is None and not is_car_build(w)):
        return ''
    if w.get('do') == 'demolish':
        return '给车下牌'
    if w.get('do') != 'build':
        return ''
    return '给车上牌' if w.get('car') is not None else '在车行登记车型'


# 0.4.13 报问题（小镇 11-25 feat/1125-isle-bugreport，开关默认开）：{"do":"report","about":"bug","why":"哪里不对"} 报岛上不对劲的，
# 原样交给岛上（回「收到了，修好会告诉你」，修好了小镇叫醒她告诉她）；不带 about 的 report 照旧是举报店铺。about 认的词和岛上 bugs.ABOUT 一样，
# 和 DSH lib/isle-tools.js 的 reportWhat 同一套。
BUG_ABOUT = frozenset({'bug', 'bugs', '问题', '报问题', '毛病', '故障', 'bug报告', '报bug'})


def report_what(w) -> str:
    """report 给主人看、记日记的中文名（不是 report 就是 ''）。"""
    if not isinstance(w, dict) or w.get('do') != 'report':
        return ''
    about = ''.join(str(w.get('about') or '').split()).lower()
    return '报岛上的问题' if about in BUG_ABOUT else '举报一家店'


TOPIC_ZH = {'我的': '你自己的', '地': '挂着的地', '楼': '待承包的楼', '铺位': '空铺位', '店': '开着的店', '订单': '接力订单', '缺的': '最缺的',
            '统计': '镇上统计', '镇规': '镇规', '怎么做': '怎么做', '我的店': '我的店', '价': '价钱', '地图': '地图', '样子': '那儿的样子', '田': '田', '镇民': '镇民', '玩的': '能玩的地方'}
VERB_ZH = {
    'fx_buy': '换鲸币', 'fx_sell': '卖鲸币', 'fx_cancel': '撤卖鲸币的单', 'bid': '出价', 'withdraw': '撤回出价', 'claim': '圈地', 'confirm_claim': '递圈地申请',
    'take_building': '承包楼', 'field_take': '包田', 'field_quote': '问田的价', 'plant': '种地', 'harvest': '收庄稼', 'field_release': '退田', 'lease_building': '租铺位',
    'rent_unit': '租床位或整栋楼', 'offer_unit': '把楼租出去', 'live': '住进一栋楼', 'open': '开店', 'shop': '经营店', 'fund': '往店里放鲸币', 'facade': '改门面',
    'sprite_set': '画整套立绘', 'sprite_tall': '改身高', 'walk': '走过去', 'arcade': '去电玩城玩', 'treat_self': '去小店吃喝', 'sign_up': '报名活动', 'borrow': '借鲸币', 'repay': '还鲸币', 'demolish': '拆楼',
    'bank': '存取银行', 'sell': '把地和楼挂出去卖', 'unsell': '撤下挂着卖的地', 'front_door': '挪正门',   # 0.4.11（小镇 11-23）
    'reply': '回客人或店里问你的问题', 'replies': '回店里问你的问题',     # 0.4.15（小镇 11-26 #33：店里问你的问题也用 reply 回）
}
TEXT_MAX = 1500
FULL_MAX = 6000                  # 「怎么做」里的全文（开店、写程序、建模）岛上一次给全，这里也不截（0.4.9）
# 0.4.9 开店、住、出租（主人 11-23「开店说明要写清楚在插件里」「产业链很重要」）：说明里只放一句指路（120 字内），岛上一层一层给：
# 「开店」总览 + 她到第几步，「开店 拿楼」查一步，「开店全文」一次看完。这一句原样写在 main.py「在岛上做一件事」的说明里
# （AstrBot 的工具说明是函数的文档字符串，只能写字面的；测试查两边一字不差），和 DSH 插件 lib/isle-tools.js 的 GUIDE_POINTER 也一字不差。
# 0.4.9 小镇 11-24：新开的店楼里要先有室内（岛上 doorway.need），说明里一句说清，免得她承包了楼才发现开不了；怎么做室内在岛上查。
GUIDE_POINTER = '开店、住、出租：先用 fishbowl_isle_look 查「怎么做」、verb 填「开店」看总览，再按总览查那一步（比如「开店 拿楼」）。一栋楼只做一样，开店前楼里要有室内；同一个主人的鱼的楼直接住、开店，不收租。'
RETRY_S = 2.0

NOT_YET = '小镇那边还没开这个（主聊天里看岛上、在岛上做事，要等小镇更新）。现在想在岛上看什么、做什么，用 fishbowl_town_go 把打算写下，小镇给你的回合里去办。'
BUILD_HERE = ('盖楼不在这里做，也不在小镇的回合里做：盖楼要和主人一起，用「一起盖楼」fishbowl_town_build（do="start"、lot 填地号）在这个私聊里和他商量，'
              '插件用 Blender 做出来、把预览图发给他看；他说「交吧」才交上岛，报价出来他说「盖吧」才扣鲸币。楼里的室内也一样（do="start" 再写 interior=true、floor 第几层）。')
HONEST = '（这是岛上刚回你的原话。办成了就照这句告诉主人；没办成就照实说没办成、为什么，别说成办了。）'


def clip(v, n: int) -> str:
    t = str(v if v is not None else '').strip()
    return t[:n - 1] + '…' if len(t) > n else t


def isle_fail(error) -> str:
    """小镇没接（不是 200）→ 一句白话；旧小镇没有这个口子（404 / 405，不是 TOWN_OFF）→ NOT_YET。"""
    if isinstance(error, TownError):
        if error.code == 'TOWN_OFF':
            return '小镇这会儿没对外来的鱼开放，岛上的事看不了也做不了。'
        if error.status in (404, 405):
            return NOT_YET
        if error.status == 503:
            return f"小镇这会儿太忙，过一会儿再试{f'（约 {int(error.retry_after)} 秒）' if error.retry_after else ''}。"
        if error.code in ('NETWORK', 'TOWN_RESTARTING', 'TOWN_OUTSIDE_NOT_CONNECTED'):
            return '这会儿还没连上小镇（或者小镇在重启），过几秒再试一次。'
        if error.code == 'TOWN_RATE_LIMITED':
            return '小镇说太频繁了，过一会儿再试。'
        return f"小镇没接（{error.code}{'：' + clip(error.reason, 80) if error.reason else ''}）。"
    return f'出了点错，没办成：{clip(error, 80)}。'


def _obj(v):
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return None
    return v if isinstance(v, dict) else None


def read_of(topic: str = '', near: str = '', spot: str = '', verb: str = '', good: str = '', page=0, more='') -> dict:
    """工具参数 -> 小镇的 read：topic（默认「我的」）+ 筛选 + page；more（JSON）里的别的筛选也带上。"""
    t = str(topic or '').strip()
    read = {'topic': t if t in LOOK_TOPICS else '我的'}
    for src in (_obj(more) or {}, {'near': near, 'spot': spot, 'verb': verb, 'good': good, 'page': page}):
        for k, v in src.items():
            if k in ('topic', 'more'):
                continue
            if isinstance(v, str) and v.strip():
                read[k] = v.strip()[:60]
            elif isinstance(v, bool):
                if k != 'page':
                    read[k] = v
            elif isinstance(v, (int, float)) and v == v and (k != 'page' or v >= 1):
                read[k] = int(v) if k == 'page' else v
    return read


def world_of(world) -> dict | None:
    """工具参数 world（JSON 字符串或对象，也收多包了一层的 {"world":{…}}）-> 小镇的 world；没写 do 回 None。"""
    w = _obj(world)
    if w and isinstance(w.get('world'), dict):
        w = w['world']
    if not w:
        return None
    verb = w.get('do').strip() if isinstance(w.get('do'), str) else ''
    if not verb or verb == 'none':
        return None
    return {**w, 'do': verb}


def new_ref(now: float | None = None) -> str:
    """插件自己起的单号（小镇：同一条鱼同一个 ref 只办一次；1–40 位 A-Za-z0-9_-）。"""
    return f'astr-{int((now or time.time()) * 1000):x}-{secrets.token_hex(3)}'


def _retryable(error) -> bool:
    return isinstance(error, TownError) and (error.code == 'NETWORK' or error.status >= 500)


async def look(client, read: dict) -> str:
    """看岛上：岛上回的那一页原样给她（标上查的是什么）；带图的页只给图上画的是什么。"""
    if client is None or not hasattr(client, 'isle_look'):
        return NOT_YET
    try:
        r = await client.isle_look(read)
    except TownError as error:
        return isle_fail(error)
    if not isinstance(r, dict):
        return '岛上没回话，过一会儿再查。'
    if r.get('ok') is False:
        old = '\n（「我的」岛上还没开，先查「我的店」，或者「地」「楼」「田」。）' if read.get('topic') == '我的' and 'topic 只能是' in str(r.get('text') or '') else ''
        return f"没查到：{clip(r.get('text') or r.get('code') or '岛上没说为什么', TEXT_MAX)}{old}"
    pics = pics_of(r)
    note = '\n'.join(['', '', NO_PICS_HEAD, *[f'{CIRCLED[k]} {p["alt"] or "（没写画的是什么）"}' for k, p in enumerate(pics)]]) if pics else ''
    return f"【岛上 · {TOPIC_ZH.get(read['topic'], read['topic'])}】\n{clip(r.get('text') or '（这一页是空的）', FULL_MAX if read.get('topic') == '怎么做' else TEXT_MAX)}{note}"


# 0.4.15 画廊：她的 gallery_confirm 插件自己办（岛上不收她直接写的）；GALLERY_CLOSED = 小镇还没开画廊时她看到的，不是报错。
# GALLERY_HOW 原样写在 main.py fishbowl_isle_act 的说明里（AstrBot 的工具说明就是那段 docstring，测试会比对）
GALLERY_CLOSED = '画廊还没开。什么都没扣。'
GALLERY_HOW = '画廊、方子：查「怎么做」、verb 填「画廊」。试画先报价，主人在这里说了「画吧」再写 {"do":"gallery_confirm","quote":"报价号"}，那时才扣。拿主人发的图当参考图：ref 写「主人的图」。'


async def act(client, world, owner_words: str = '', journal=None, sleep=asyncio.sleep, gallery=None, consent=None, owner_pic=None) -> str:
    """在岛上做一件事：岛上回的那句话原样给她，框上「照实告诉主人」。连不上 / 岛没答上来：同一个 ref 再发一次（岛上只办一次）。
    0.4.15 画廊：gallery = gallery.Gallery；consent(since) -> {'why'} | {'words'}（cobuild 那一套：主人私聊、报价以后、他亲手打的、像点头）；
    owner_pic() -> (字节, 类型) | None（主人 30 分钟内在私聊里发的最后一张图）。"""
    w = world_of(world)
    if not w:
        return '写一件要做的事：world 填 {"do":"…", …}（和小镇回合里写的 world 一样）。不会写先用 fishbowl_isle_look 查「怎么做」。'
    verb = w['do']
    if verb in COBUILD and not is_car_build(w):
        return BUILD_HERE
    if verb == 'look':
        return '查看用 fishbowl_isle_look，不算做事。'
    if verb == 'gallery_confirm':
        return await confirm_try(client, w, journal=journal, gallery=gallery, consent=consent)
    w2 = w
    if verb == 'gallery' and is_owner_pic_ref(w.get('ref')):
        up = await upload_owner_pic(client, gallery, owner_pic)
        if up.get('why'):
            return up['why']
        w2 = {**w, 'ref': up['id']}
    words = owner_words.strip() if isinstance(owner_words, str) else ''      # 0.4.15：主人的原话不截
    what = car_what(w) or report_what(w) or VERB_ZH.get(verb) or f'do={clip(verb, 24)}'
    if verb in OWNER_MONEY and not words:
        return f'{what}花的是主人给你的饭票：要主人在这段聊天里叫你做、或者你问了他点了头才办，owner_words 填他的原话。他没提就先别办，也不要劝他充值。什么都没扣。'
    if client is None or not hasattr(client, 'isle_act'):
        return NOT_YET

    def note(t):
        try:
            if journal is not None:
                return journal.event('self', t) or ''
        except Exception:  # noqa: BLE001 只是记一笔
            pass
        return ''

    ref = new_ref()
    r = None
    for k in range(2):
        try:
            r = await client.isle_act(w2, ref)
        except TownError as error:
            if k == 0 and _retryable(error):                 # 不知道岛上收没收到：同一个 ref 再发一次
                await sleep(min(5.0, float(error.retry_after or 0)) or RETRY_S)
                continue
            why = isle_fail(error)
            if why != NOT_YET:
                note(f'你在聊天里想在岛上{what}，没办成：{clip(why, 60)}')
            return why
        if k == 0 and isinstance(r, dict) and r.get('ok') is False and r.get('code') == 'unavailable':
            await sleep(RETRY_S)                               # 岛没答上来：同一个 ref 再问一次
            continue
        break
    if not isinstance(r, dict):
        return f'岛上没回话，不知道办没办成：先用 fishbowl_isle_look 查「我的」看一眼。{HONEST}'
    ok = r.get('ok') is not False
    said = clip(r.get('text') or ('办了' if ok else '没办成'), TEXT_MAX)
    src = note(f"你在聊天里{'' if ok else '想'}在岛上{what}{'' if ok else '，没成'}：{clip(said, 80)}{f'（主人说：「{clip(words, 40)}」）' if words else ''}")
    # 0.4.14（D）：办成的钉进接下来的小镇回合（主人在场定的，那边别改它）；要走的到了就松开，当场办完的带进一个回合就松开
    if ok and journal is not None and hasattr(journal, 'pin'):
        try:
            journal.pin(f'{what}：{clip(said, 60)}', eta_min=eta_of(said), src=src)
        except Exception:  # noqa: BLE001 只是记一笔
            pass
    queued = ok and r.get('code') == 'queued'   # 0.4.13：远处下单先出发，到了才办（小镇 auto-transit 11-24），这会儿还没办完
    # 0.4.15 画廊试画报价：插件记下报价号，她写 gallery_confirm 时拿来核对主人的话
    q = gallery.quoted(r) if gallery is not None and r.get('code') == 'quoted' else None
    if q:
        coin = f"，{q['coin']} 鲸币" if q.get('coin') else ''
        return (f"岛上回你的：「{said}」\n只报了价，还没扣：报价号 {q['quote']}{coin}。把价告诉主人；他在这里说了「画吧」，"
                f'再写 {{"do":"gallery_confirm","quote":"{q["quote"]}"}}，那时才扣（2 小时内有效）。他不说就不用管。{HONEST}')
    head = '还没办完：出发了，到了照办，办好了小镇下一回合告诉你' if queued else '办成了' if ok else f"没办成（{clip(r.get('code') or '没说', 24)}）"
    return f'岛上回你的：「{said}」\n{head}。{HONEST}'


def _note(journal, t) -> str:
    try:
        if journal is not None:
            return journal.event('self', t) or ''
    except Exception:  # noqa: BLE001 只是记一笔
        pass
    return ''


async def confirm_try(client, w: dict, *, journal=None, gallery=None, consent=None) -> str:
    """0.4.15 画廊：主人点了头的试画（报价以后、他亲手打的字、像点头），插件交给岛上确认。"""
    if gallery is None or client is None or not hasattr(client, 'gallery_confirm'):
        return NOT_YET
    want = w.get('quote').strip() if isinstance(w.get('quote'), str) else ''
    q = gallery.quote(want)
    if not q:
        if want:
            return f'插件这里没有在等的报价 {clip(want, 16)}（或者已经过了 2 小时）：先重新试一次拿新报价（{{"do":"gallery","recipe":"R-…","try":1,"wish":"…"}}）。什么都没扣。'
        return '现在没有在等主人点头的试画报价：先写 {"do":"gallery","recipe":"R-…","try":1,"wish":"…"} 拿报价。什么都没扣。'
    ok = consent(q['at']) if callable(consent) else {'why': NOT_YET}
    if ok.get('why'):
        return ok['why']
    try:
        r = await client.gallery_confirm(q['quote'], ok['words'])
    except TownError as error:
        if error.status == 404:
            return GALLERY_CLOSED
        return isle_fail(error)
    if not isinstance(r, dict):
        return f'岛上没回话，不知道确认了没有：先用 fishbowl_isle_look 查「我的」看一眼。{HONEST}'
    said = clip(r.get('text') or '', TEXT_MAX)
    if r.get('ok') is False:
        _note(journal, f"你在聊天里想确认试画，没成：{clip(said or r.get('code'), 60)}")
        if r.get('code') == 'gallery_off':        # 画廊还没对她开（岛上 OFF_TEXT / 只给测试的鱼）；closed 是「这家画廊关了」，照原话说
            return GALLERY_CLOSED
        if r.get('code') in ('old', 'none'):      # 岛上说作废了 / 没有这个号：插件这边也不再等它
            gallery.confirmed(q['quote'])
        again = ('报价过期了（过了 2 小时、有了新报价、或者方子删了），重新试一次拿新报价。' if r.get('code') == 'old'
                 else '岛上没认主人这句话是点头，等他明确说「画吧」再确认。' if r.get('code') == 'owner_said' else '')
        return f"岛上回你的：「{said or '没确认'}」\n没确认（{clip(r.get('code') or '没说', 24)}）。{again}什么都没扣。{HONEST}"
    gallery.confirmed(q['quote'])
    paid = f"，{r['paid']} 鲸币" if r.get('paid') is not None else ''
    src = _note(journal, f"你在聊天里确认了试画（{q['quote']}{paid}；主人说：「{clip(ok['words'], 40)}」）：{clip(said, 60)}")
    if journal is not None and hasattr(journal, 'pin'):
        try:
            journal.pin(f"确认了试画：{clip(said or q['quote'], 60)}", src=src)
        except Exception:  # noqa: BLE001 只是记一笔
            pass
    took = f"，扣了 {r['paid']} 鲸币" if r.get('paid') is not None else ''
    return f"岛上回你的：「{said or '开画了'}」\n确认了（主人说「{clip(ok['words'], 40)}」{took}）。画好了挂上墙当样图，小镇会告诉你；没画成全退。{HONEST}"


async def upload_owner_pic(client, gallery, owner_pic) -> dict:
    """0.4.15 画廊：她写 "ref":"主人的图"：把主人 30 分钟内在私聊里发的最后一张图传上去，换成 u_ 号。→ {'id'} | {'why'}。"""
    if gallery is None or client is None or not hasattr(client, 'gallery_upload') or not callable(owner_pic):
        return {'why': NOT_YET}
    try:
        got = await owner_pic()
    except Exception:  # noqa: BLE001 图读不出来（链接过期、下载失败）
        return {'why': '主人发的那张图插件读不出来（可能过期了），请他再发一次。什么都没传。'}
    if not got:
        return {'why': '主人最近 30 分钟没在这里发图：等他在私聊里发了图，再写 "ref":"主人的图"。什么都没传。'}
    data, ctype = got
    if not data:
        return {'why': '主人发的那张图是空的，请他再发一次。什么都没传。'}
    if len(data) > UPLOAD_MAX:
        return {'why': '主人发的那张图超过 8 MB，岛上不收；请他发小一点的。什么都没传。'}
    ctype = ctype or image_type(data) or 'image/jpeg'
    try:
        r = await client.gallery_upload(data, ctype)
    except TownError as error:
        if error.status == 404:
            return {'why': GALLERY_CLOSED}
        if error.status == 413:
            return {'why': '主人发的那张图太大，岛上不收；请他发小一点的。什么都没传。'}
        return {'why': isle_fail(error)}
    if not isinstance(r, dict) or r.get('ok') is False or not isinstance(r.get('upload_id'), str):
        if isinstance(r, dict) and r.get('code') == 'busy':
            return {'why': '一小时里传上去还没用掉的图已经有 12 张了：先把传上去的用掉，过一会儿再传。'}
        why = (r.get('text') or r.get('code')) if isinstance(r, dict) else ''
        return {'why': f"岛上没收这张图：{clip(why or '没说为什么', 80)}。"}
    return {'id': r['upload_id']}
