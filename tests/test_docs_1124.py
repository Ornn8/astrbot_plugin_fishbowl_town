"""0.4.9 说明跟上小镇 11-24 这一批（地租按成交价、汇率每天动、地价跟着成交价、面多文件大另收、开店要室内、探店、镇民四层、闲置收回）：
一份清单，查新手引导、README、工具说明里是岛上的原话，老说法（「每天交一点地租」这类）一处都没留。和 DSH 插件 0.9.12 test/docs-1124.test.mjs 同一份。"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town import isle_tools as T  # noqa: E402
from astrbot_plugin_fishbowl_town.guide import BUILD_STEP, FX_RULE, LAND_RULE, RENT_RULE, guide_text  # noqa: E402

README = (ROOT / 'README.md').read_text(encoding='utf-8')
MAIN = (ROOT / 'main.py').read_text(encoding='utf-8')


def tool_doc(name):
    m = re.search(r"@filter\.llm_tool\(name='" + name + r"'\)\n    async def \w+\(self, event: AstrMessageEvent[^)]*\):\n        \"\"\"([\s\S]*?)\"\"\"", MAIN.replace('\r\n', '\n'))
    assert m, name
    return m.group(1)


SAID = {'新手引导': guide_text(), 'README': README, '做一件事': tool_doc('fishbowl_isle_act'), '看岛上': tool_doc('fishbowl_isle_look'),
        '一起盖楼': tool_doc('fishbowl_town_build')}


def test_the_islands_own_sentences_rent_paid_price_moving_rate_land_values():
    assert RENT_RULE == '每天地租 = 成交价的 0.1%（按地价算的更高就按地价）'                         # land.rent_note()（0.4.11：11-23 减半）
    assert FX_RULE == '汇率每天收盘调一次：当天买鲸币的多就涨、卖的多就跌，一天最多涨跌 5%；离起步价 ¥0.004 越远，往回拉得越多，最多偏 30%。'   # exchange.rule_text()
    assert LAND_RULE == '地价每天按附近 14 天的成交价调：成交价高于底价就涨，流拍、退地就跌，一天最多变 5%，最多到原价的 3 倍、最少 0.7 倍。'   # market NOTE
    for where, text in (('新手引导', guide_text()), ('README', README)):
        for rule in (RENT_RULE, FX_RULE, LAND_RULE):
            assert rule in text, (where, rule[:12])
        assert '汇率每天收盘调一次' in text and '成交价的 0.1%' in text and '0.2%' not in text, where


def test_no_old_wording_left():
    for where, text in SAID.items():
        for old in ('每天交一点地租', '交一点地租', '一点物业', '退她七成', '田 7 天没种东西也一样', '赚到的鲸币', '都住奶蛙公寓', '现成的楼本来就有', '这一版还做不了'):
            assert old not in text, (where, old)


def test_idle_land_as_the_island_runs_it():
    g = guide_text()
    # 小镇 11-24「什么算在用」（店主「按你的倾向改」）：在做的事算在用；承包的楼空满 14 天收回挂拍，卖出给七成（不再是「承包费不退」）
    assert '空地 7 天没楼、没在盖（和主人做着草稿、报价交上去 7 天内都算）、也没开店，地租翻倍（多的那份记成闲置费）；14 天镇上收回拿去拍卖，卖出后她得成交价的七成' in g
    assert '田 7 天没种着东西（熟了不收也算）就收回拍卖' in g
    assert '承包的楼空着 14 天（没开东西、没住、没整栋租出去，也没在做室内、重盖）也收回拿去拍卖，卖出后一样得七成' in g
    assert '承包费不退' not in g
    assert '卖出后她得成交价的七成' in README


def test_heavy_models_allowance_extra_price_and_daily_upkeep_are_stated():
    assert '起步价里含一定的高度、体积、面数和文件大小，超出的另收；面数多、文件大的，每天的物业也跟着多' in BUILD_STEP
    assert '一项一项写着怎么算' in BUILD_STEP
    # 0.4.11：小镇 11-23 下午降价（价目表 building，和 build_guide 里的数一样）
    for w in ('起步 62.5 鲸币，含 20 米高、20000 立方米', '80000 面、2048 KB', '每一万面 50 鲸币', '每 MB 100 鲸币', '文件超出起步的每 MB 每天 2 鲸币', '面数超出起步的每一万面每天 1 鲸币'):
        assert w in README, w


def test_shops_need_an_inside_one_line_in_the_pointer_rules_and_prices_in_the_readme_and_this_version_makes_it():
    assert '开店前楼里要有室内' in T.GUIDE_POINTER and len(T.GUIDE_POINTER) <= 120
    assert T.GUIDE_POINTER in tool_doc('fishbowl_isle_act')
    g = guide_text()
    assert '新开的店（游戏、场子、工作站也一样）楼里要先有室内' in g
    assert '室内也是你和她一起做（见「在岛上盖楼」）' in g
    assert '开店、开游戏要楼里有室内：也是你们一起做，一层一份' in BUILD_STEP and '开店、开游戏要楼里有室内：也是你们一起做，一层一份' in README
    assert '**楼里的室内（0.4.9）**' in README
    assert '你说「交吧」才交、说「就这样」才扣鲸币（怎么做见上面「楼里的室内」）' in README
    assert not re.search(r'做室内要 0\.4\.9 以上|这一版（0\.4\.[89]）还做不了', README + g)
    for w in ('室内跟着外面的楼走：几层就做几层，每层不比外面大，面数和文件按面积给', '起步 20 鲸币 + 每平方米 0.2 鲸币', '物业每天每平方米 0.01 鲸币'):   # 0.4.11：11-23 下午降价
        assert w in README, w


def test_tan_dian_the_residents_topic_and_no_push_to_spend():
    assert '探店（门面、屋里、东西）：写不写随她，写了你在岛的页面「我的鱼」里看得到，每项能打 1–5 星' in guide_text()
    assert '镇民' in T.LOOK_TOPICS and T.read_of('镇民')['topic'] == '镇民'
    assert '镇民（四层镇民各要什么、住哪）' in tool_doc('fishbowl_isle_look')
    for where, text in SAID.items():
        if where != 'README':
            assert not re.search('赚|划算|趁|该买|该卖|充值吧|去充值', text), where
