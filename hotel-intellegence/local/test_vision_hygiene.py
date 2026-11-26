from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from local.vision_model import FACILITIES, VISIBILITY, finding, inspect_floor, inspect_group


class HygieneTests(unittest.TestCase):
    def test_absent_obstructed_and_visible_facilities(self):
        for name in FACILITIES:
            for visibility, proposed, expected in [
                (VISIBILITY[0], '无法判断', '未见异常'),
                (VISIBILITY[2], '未见异常', '无法判断'),
                (VISIBILITY[1], '发现异常', '发现异常'),
            ]:
                with self.subTest(name=name, visibility=visibility):
                    status, _ = finding(dict(visibility=visibility, status=proposed, detail='模型说明'), name)
                    self.assertEqual(status, expected)

    def test_unobservable_is_not_equivalent_to_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'offline.jpg'
            Image.new('RGB', (320,240)).save(p)
            result = inspect_group(['地面垃圾', *FACILITIES], [p], [None], '')
        self.assertTrue(all(r['status']=='无法判断' for r in result))

    def test_video_litter_visible_only_in_later_sample(self):
        answers = [{'detail':'未见异常'}, {'detail':'一张纸散落在地面上。'}, {'observation':'有垃圾'}]
        with patch('local.vision_model.ask_vision', side_effect=answers):
            status, detail = inspect_floor([Path('a.jpg'), Path('b.jpg')], [0, .5], '')
        self.assertEqual(status,'发现异常')
        self.assertIn('0.5 秒', detail)
