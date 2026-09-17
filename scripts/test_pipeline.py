"""Behavioral smoke tests using generated PDFs; no course content or network access."""
import argparse
import contextlib
import copy
from io import BytesIO, StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pdf_translate as engine
from layout_hints import background_hint, math_hint, typography_warnings


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pdf-skill-test-')
        self.root = Path(self.temp.name)
        self.source = self.root/'source.pdf'
        doc = engine.fitz.open()
        first = doc.new_page(width=500, height=400)
        first.insert_text((30, 45), 'Learning objectives', fontsize=24)
        first.insert_text((30, 100), 'Understand the expected value.', fontsize=18)
        first.insert_text((30, 180), 'E(X) = 2', fontsize=20)
        image = engine.Image.new('RGB', (120, 80), '#ffcc66')
        engine.ImageDraw.Draw(image).text((10, 10), 'Table 1', fill='black')
        buf = BytesIO()
        image.save(buf, format='PNG')
        first.insert_image(engine.fitz.Rect(300, 240, 420, 320), stream=buf.getvalue())
        second = doc.new_page(width=500, height=400)
        second.insert_text((30, 45), 'Next slide', fontsize=24)
        second.insert_text((30, 100), 'X = 1', fontsize=20)
        doc.save(self.source)
        doc.close()
        self.job = self.root/'job'
        with contextlib.redirect_stdout(StringIO()):
            engine.prepare(argparse.Namespace(input=self.source, job=self.job, target='zh-CN', no_preview=True))
        self.plan = engine.read_json(self.job/'plan.json')
        pages = engine.read_json(self.job/'manifest.json')['pages']
        self.formula = pages[0]['blocks'][2]['id']
        self.plan['edits'] = [
            {'refs': [pages[0]['blocks'][0]['id']], 'html': '学习目标', 'box': [30, 20, 450, 60], 'font_size': 24},
            {'refs': [pages[0]['blocks'][1]['id']], 'html': '理解<b>期望值</b>。', 'box': [30, 78, 450, 120], 'font_size': 18},
            {'refs': [pages[1]['blocks'][0]['id']], 'html': '下一页', 'box': [30, 20, 450, 60], 'font_size': 24}]
        self.plan['keep'] = [{'refs': [pages[0]['blocks'][2]['id'], pages[1]['blocks'][1]['id']], 'reason': 'Preserve equations'}]
        self.output = self.root/'result'

    def tearDown(self):
        self.temp.cleanup()

    def apply(self):
        engine.write_json(self.job/'plan.json', self.plan)
        with contextlib.redirect_stdout(StringIO()):
            engine.apply(argparse.Namespace(job=self.job, out_dir=self.output, mode='both', font=None, bold_font=None, overwrite=False))

    def test_complete_translation_and_alternating_pages(self):
        self.apply()
        qa = engine.read_json(self.job/'qa.json')
        self.assertEqual(qa['automatic_checks'], 'pass')
        self.assertEqual(qa['visual_review'], 'pending_agent_review')
        with engine.open_pdf(self.output/'source_zh-CN.pdf') as translated:
            self.assertIn('学习目标', translated[0].get_text())
            self.assertIn('E(X) = 2', translated[0].get_text())
            self.assertNotIn('Learning objectives', translated[0].get_text())
        with engine.open_pdf(self.output/'source_original-zh-CN.pdf') as paired:
            self.assertEqual(len(paired), 4)
            self.assertIn('Learning objectives', paired[0].get_text())
            self.assertIn('学习目标', paired[1].get_text())
            self.assertIn('Next slide', paired[2].get_text())
            self.assertIn('下一页', paired[3].get_text())

    def test_missing_translation_rejected(self):
        self.plan['edits'].pop()
        with self.assertRaisesRegex(ValueError, 'no edit/keep'):
            self.apply()
        self.assertFalse(self.output.exists())

    def test_duplicate_ref_rejected(self):
        self.plan['edits'].append(copy.deepcopy(self.plan['edits'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate coverage'):
            self.apply()

    def test_stale_source_rejected(self):
        self.plan['source_sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'hash changed'):
            self.apply()

    def test_overflow_does_not_publish_pdf(self):
        self.plan['edits'][0]['html'] = '测试' * 2000
        with self.assertRaisesRegex(ValueError, 'overflow'):
            self.apply()
        self.assertEqual(list(self.output.glob('*.pdf')), [])

    def test_formula_overlap_rejected(self):
        self.plan['edits'][1]['box'][3] = 200
        with self.assertRaisesRegex(ValueError, 'overlaps another text'):
            self.apply()

    def test_image_content_overlap_rejected(self):
        self.plan['edits'][1]['box'] = [250, 90, 450, 340]
        with self.assertRaisesRegex(ValueError, 'visible image pixels'):
            self.apply()

    def test_outside_page_rejected(self):
        self.plan['edits'][0]['box'][2] = 501
        with self.assertRaisesRegex(ValueError, 'outside page'):
            self.apply()

    def test_html_external_resource_rejected(self):
        self.plan['edits'][0]['html'] = '<img src="https://example.com/image.png">'
        with self.assertRaisesRegex(ValueError, 'HTML tag'):
            self.apply()

    def test_missing_glyph_rejected(self):
        self.plan['edits'][0]['html'] = '\U0010ffff'
        with self.assertRaisesRegex(ValueError, 'lacks characters'):
            self.apply()

    def test_interleave_count_mismatch(self):
        single = engine.fitz.open()
        single.new_page(width=500, height=400)
        with engine.open_pdf(self.source) as source:
            with self.assertRaisesRegex(ValueError, 'equal page counts'):
                engine.make_interleaved(source, single, self.root/'bad.pdf')
        single.close()

    def test_source_overwrite_prohibited(self):
        with self.assertRaisesRegex(ValueError, 'overwrite a source'):
            engine.ensure_outputs([self.source], [self.source], True)

    def test_rotated_page_rejected(self):
        with engine.open_pdf(self.source) as doc:
            doc[0].set_rotation(90)
            rotated = self.root/'rotated.pdf'
            doc.save(rotated)
        with engine.open_pdf(rotated) as doc:
            font, bold = engine.fonts()
            with self.assertRaisesRegex(ValueError, 'rotated pages'):
                engine.build_plan(doc, self.plan, font, bold)

    def test_scaffold_covers_source_without_overwriting_plan(self):
        original_plan = (self.job/'plan.json').read_bytes()
        draft = engine.read_json(self.job/'plan.template.json')
        manifest = engine.read_json(self.job/'manifest.json')
        expected = {b['id'] for p in manifest['pages'] for b in p['blocks'] if b['text'].strip()}
        found = [r for entry in draft['edits']+draft['keep'] for r in entry['refs']]
        self.assertEqual(set(found), expected)
        self.assertEqual(len(found), len(set(found)))
        self.assertEqual((self.job/'plan.json').read_bytes(), original_plan)
        self.assertTrue(draft['review_required'])
        self.assertIn(self.formula, [r for k in draft['keep'] for r in k['refs']])
        self.plan = draft
        with self.assertRaisesRegex(ValueError, 'review_required'):
            self.apply()

    def test_nonwhite_background_detected_and_applied(self):
        doc = engine.fitz.open()
        page = doc.new_page(width=500, height=400)
        page.draw_rect([20, 20, 460, 100], fill=engine.color('#f1dcdb'), color=None)
        page.insert_text((40, 60), 'Learning objectives', fontsize=24)
        doc.save(self.root/'colored.pdf')
        doc.close()
        with engine.open_pdf(self.root/'colored.pdf') as doc:
            layout = engine.layout(doc, hints=True)
            block = layout[0]['blocks'][0]
            self.assertEqual(block['suggested_fill'], '#f1dcdb')
            self.assertEqual(block['fill_method'], 'vector_and_pixels')
            font, bold = engine.fonts()
            plan = {'edits': [{'refs': [block['id']], 'html': '学习目标', 'box': [40, 35, 440, 80]}], 'keep': []}
            records, _ = engine.build_plan(doc, plan, font, bold)
            self.assertEqual(records[0]['fill'], '#f1dcdb')

    def test_mixed_background_abstains(self):
        doc = engine.fitz.open()
        page = doc.new_page(width=500, height=400)
        page.draw_rect([0, 0, 250, 400], fill=(1, 0, 0), color=None)
        page.draw_rect([250, 0, 500, 400], fill=(0, 0, 1), color=None)
        hint = background_hint(page, [100, 100, 400, 150])
        self.assertIsNone(hint['suggested_fill'])
        doc.close()

    def test_inline_math_prose_not_automatically_kept(self):
        block = {'text': 'The mean is μ and the variance is σ².',
                 'spans': [{'text': 'The mean is ', 'font': 'Calibri'},
                           {'text': 'μ', 'font': 'CambriaMath'},
                           {'text': ' and the variance is σ².', 'font': 'Calibri'}]}
        self.assertEqual(math_hint(block)['suggested_action'], 'translate')

    def test_orphan_and_punctuation_are_warnings(self):
        doc = engine.fitz.open()
        page = doc.new_page(width=500, height=400)
        page.insert_text((30, 70), '这里有一段文字', fontname='china-s', fontsize=20)
        page.insert_text((30, 98), '，量', fontname='china-s', fontsize=20)
        records = [{'page': 1, 'refs': ['p001.b000'], 'box': [25, 45, 450, 120], 'font_size': 20}]
        codes = {x['code'] for x in typography_warnings(page, records)}
        self.assertIn('orphan_character_detected', codes)
        self.assertIn('line_start_punctuation', codes)
        doc.close()

    def test_interleave_after_different_scale_rendering(self):
        with engine.open_pdf(self.source) as left, engine.open_pdf(self.source) as right:
            for page in left:
                engine.image_for(page, 1.5)
            for page in right:
                engine.image_for(page, 2)
            engine.make_interleaved(left, right, self.root/'scales.pdf')
        with engine.open_pdf(self.root/'scales.pdf') as doc:
            self.assertEqual(len(doc), 4)

    def test_inline_comma_after_formula_is_not_line_start(self):
        doc = engine.fitz.open()
        page = doc.new_page(width=500, height=400)
        page.insert_text((30, 70), 'X = 1', fontsize=20)
        page.insert_text((100, 70), '，则成立', fontname='china-s', fontsize=20)
        records = [{'page': 1, 'refs': ['p001.b001'], 'box': [99, 45, 450, 85], 'font_size': 20}]
        self.assertNotIn('line_start_punctuation', {x['code'] for x in typography_warnings(page, records)})
        doc.close()

    def test_large_font_triggers_verified_fallback(self):
        doc = engine.fitz.open()
        page = doc.new_page(width=500, height=400)
        font, _ = engine.fonts()
        page.insert_font(fontname='custom', fontfile=str(font))
        page.insert_text((30, 60), '学习目标', fontname='custom', fontsize=24)
        native_subset = engine.fitz.Document.subset_fonts
        def simulate_native_miss(document, verbose=False, fallback=False):
            if fallback:
                return native_subset(document, verbose=verbose, fallback=True)
        with patch.object(engine.fitz.Document, 'subset_fonts', simulate_native_miss):
            result = engine.save_compact(doc, self.root/'compact.pdf')
        self.assertEqual(result['method'], 'native_then_fonttools')
        self.assertLess(result['final_bytes'], result['native_bytes']/2)
        self.assertFalse(result['fonts']['large_font_remaining'])
        doc.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
