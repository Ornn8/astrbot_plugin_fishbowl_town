"""0.4.15 拍照店家（小镇 feat/1126-photo-studio）：相册里店家拍的照片下面原样那一句「由「暖光照相馆」拍」，别的照片照旧。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_fishbowl_town import render  # noqa: E402

BY = '由「暖光照相馆」拍'


def test_album_by_reads_the_towns_line_verbatim_and_nothing_else():
    assert render.album_by({'studio': {'shop': '暖光照相馆', 'by': BY}}) == BY
    assert render.album_by({'by': '由「小林画铺」画'}) == '由「小林画铺」画'
    assert render.album_by({'what': '照片'}) == ''
    assert render.album_by({'studio': 'P-0a1b2c3d'}) == ''
    assert render.album_by(None) == ''


def test_a_shop_shot_photo_has_the_by_line_right_under_it():
    out = render.album({'items': [
        {'item_id': 'a1', 'kind': 'photo', 'what': '你的胶片照', 'studio': {'shop': '暖光照相馆', 'by': BY}},
        {'item_id': 'a2', 'kind': 'photo', 'what': '你的普通照'},
    ]}, '你').split('\n')
    i = next(k for k, line in enumerate(out) if '你的胶片照' in line)
    assert out[i + 1] == '  ' + BY
    j = next(k for k, line in enumerate(out) if '你的普通照' in line)
    assert '由「' not in out[j + 1]
