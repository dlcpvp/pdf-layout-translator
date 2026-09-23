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
from layout_hints import background_hint, math_hint, typography_warnings, associate_math, layout_recommendations


class PipelineTests(unittest.TestCase):
    def test_white_on_white_rejected_before_publication(self):
        self.plan['edits'][0].update(color='#ffffff', fill='#ffffff')
        with self.assertRaisesRegex(ValueError, 'unreadable_text_contrast'):
            self.apply()
        self.assertFalse(list(self.output.glob('*.pdf')))

    def test_explicit_wrong_background_rejected(self):
        self.plan['edits'][0].update(color='#ffffff', fill='#002b52')
        with self.assertRaisesRegex(ValueError, 'background_fill_mismatch'):
            self.apply()
        self.assertFalse(list(self.output.glob('*.pdf')))

    def test_dark_background_keeps_white_text(self):
        with engine.open_pdf(self.source) as original:
            page = original[0]
            page.draw_rect(page.rect, fill=(0, .17, .32), color=None, overlay=False)
            hints = [background_hint(page, [30, 20, 250, 50])]
            self.assertEqual(hints[0]['suggested_fill'], '#002b52')
            ratio = engine.validate_edit_colors('#ffffff', '#002b52', hints, 'test')
            self.assertGreater(ratio, 10)
            with self.assertRaisesRegex(ValueError, 'background_fill_mismatch'):
                engine.validate_edit_colors('#000000', '#ffffff', hints, 'test')

    def test_context_math_keeps_notation_but_not_prose(self):
        def block(id, text, box):
            return {'id': id, 'text': text, 'bbox': box, 'spans': [{'size': 20}]}
        seed = block('seed', '√', [20, 40, 35, 65])
        seed.update(classification='math_formula', suggested_action='keep')
        fragment = block('fragment', 'Var 𝑋Var(𝑌)', [36, 40, 95, 65])
        prose = block('prose', 'and the variance', [20, 66, 95, 90])
        remote = block('remote', 'dy', [300, 40, 330, 65])
        associate_math([seed, fragment, prose, remote])
        self.assertEqual(fragment['math_context_refs'], ['seed'])
        self.assertNotIn('suggested_action', prose)
        self.assertNotIn('suggested_action', remote)

    def test_geometry_proposals_respect_obstacles_and_source_lines(self):
        block = {'id': 'a', 'text': 'Properties of', 'bbox': [20, 20, 150, 100],
                 'erase_rects': [[20, 20, 150, 50], [20, 65, 70, 100]]}
        page = {'rect': [0, 0, 400, 400], 'blocks': [block], 'images': [[90, 90, 200, 180]]}
        hints = layout_recommendations(page, block, block['bbox'], 20)
        self.assertEqual(hints, [{'kind': 'clamp_above_image', 'box': [20, 20, 150, 88]}])
        block['erase_rects'][-1][2] = 110
        self.assertEqual(layout_recommendations(page, block, block['bbox'], 20), [])
        page['images'] = []
        block['bbox'] = [20, 20, 150, 50]
        page['blocks'].append({'id': 'b', 'text': 'distributions', 'bbox': [20, 55, 160, 80]})
        hints = layout_recommendations(page, block, block['bbox'], 20)
        self.assertIn('merge_continuation', [h['kind'] for h in hints])
        page['blocks'].append({'id': 'c', 'text': 'obstacle', 'bbox': [100, 51, 130, 54]})
        self.assertNotIn('merge_continuation', [h['kind'] for h in layout_recommendations(page, block, block['bbox'], 20)])

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

    def apply(self, **options):
        engine.write_json(self.job/'plan.json', self.plan)
        args = dict(job=self.job, out_dir=self.output, mode='both', font=None, bold_font=None,
                    overwrite=False, layout='strict')
        args.update(options)
        with contextlib.redirect_stdout(StringIO()) as output:
            engine.apply(argparse.Namespace(**args))
        return engine.json.loads(output.getvalue())

    def test_draft_cache_reuses_only_unchanged_pages_and_final_checks(self):
        first = self.apply(draft=True, preview='none')
        self.assertEqual(first['typeset_pages'], [1, 2])
        self.assertFalse((self.output/'source_original-zh-CN.pdf').exists())
        draft_qa = engine.read_json(self.job/'qa-draft.json')
        self.assertEqual(draft_qa['optimization']['translated']['method'], 'deferred')
        self.plan['edits'][0]['html'] = '新的学习目标'
        second = self.apply(draft=True, preview='none', overwrite=True)
        self.assertEqual(second['reused_pages'], [2])
        self.assertEqual(second['typeset_pages'], [1])
        final = self.apply(preview='none')
        self.assertEqual(final['reused_pages'], [1, 2])
        self.assertEqual(final['typeset_pages'], [])
        self.assertFalse(final['draft'])
        self.assertTrue((self.output/'source_dual.pdf').exists())
        with engine.open_pdf(self.output/'source_zh-CN.pdf') as doc:
            self.assertIn('新的学习目标', doc[0].get_text())
            self.assertNotIn('Learning objectives', doc[0].get_text())

    def test_failures_are_aggregated_and_successful_pages_resume(self):
        good = self.plan['edits'][2]['html']
        self.plan['edits'][2]['html'] = '长文' * 2000
        with self.assertRaisesRegex(ValueError, 'overflow'):
            self.apply(draft=True, preview='none')
        report = engine.read_json(self.job/'apply-errors.json')
        self.assertEqual(report['failed_pages'], [2])
        self.assertEqual(list(self.output.glob('*.pdf')), [])
        self.plan['edits'][2]['html'] = good
        result = self.apply(draft=True, preview='none')
        self.assertEqual(result['reused_pages'], [1])
        self.assertEqual(result['typeset_pages'], [2])
        self.assertFalse((self.job/'apply-errors.json').exists())

    def test_corrupted_cache_is_ignored(self):
        self.apply(draft=True, preview='none')
        (self.job/'layout-cache.pdf').write_bytes(b'broken cache')
        result = self.apply(draft=True, preview='none', overwrite=True)
        self.assertEqual(result['reused_pages'], [])

    def test_auto_expands_short_box_without_changing_plan(self):
        self.plan['edits'][0].update(html='学习目标', box=[30, 20, 65, 60], min_scale=1)
        with self.assertRaisesRegex(ValueError, 'overflow'):
            self.apply(draft=True, preview='none')
        self.apply(draft=True, preview='none', layout='auto')
        qa = engine.read_json(self.job/'qa-draft.json')
        rec = qa['edits'][0]
        self.assertEqual(rec['layout_adjustment'], 'expand_into_blank_space')
        self.assertEqual(rec['requested_box'], [30, 20, 65, 60])
        self.assertGreater(rec['box'][2], 65)
        self.assertEqual(engine.read_json(self.job/'plan.json')['edits'][0]['box'], [30, 20, 65, 60])

    def test_auto_clamps_image_overlap_but_strict_rejects_it(self):
        self.plan['edits'][1]['box'] = [250, 90, 450, 340]
        self.apply(draft=True, preview='none', layout='auto')
        rec = engine.read_json(self.job/'qa-draft.json')['edits'][1]
        self.assertEqual(rec['box'][3], 238)
        self.assertEqual(rec['layout_adjustment'], 'clamp_above_image')

    def test_auto_extension_rejects_visible_vector_pixels(self):
        doc = engine.fitz.open()
        page = doc.new_page(width=300, height=200)
        page.draw_rect([90, 20, 120, 60], fill=(1, 0, 0), color=None)
        self.assertFalse(engine.extension_is_blank(page, [20, 20, 80, 60], [20, 20, 130, 60], '#ffffff'))
        self.assertTrue(engine.extension_is_blank(page, [20, 80, 80, 120], [20, 80, 130, 120], '#ffffff'))
        doc.close()

    def test_multiple_page_overflows_reported_together(self):
        self.plan['edits'][0]['html'] = '文本' * 1200
        self.plan['edits'][2]['html'] = '文本' * 1200
        with self.assertRaisesRegex(ValueError, 'overflow'):
            self.apply(draft=True, preview='none')
        report = engine.read_json(self.job/'apply-errors.json')
        self.assertEqual(report['failed_pages'], [1, 2])
        self.assertEqual(len(report['failures']), 2)

    def test_cache_graft_preserves_page_tree_links_and_appearance(self):
        with engine.open_pdf(self.source) as original, engine.open_pdf(self.source) as cached:
            original[0].insert_link({'kind': engine.fitz.LINK_GOTO, 'from': engine.fitz.Rect(30, 30, 90, 50), 'page': 1})
            original.set_toc([[1, 'Second page', 2]])
            cached[0].insert_text((30, 220), 'Cached addition', fontsize=12)
            before = engine.image_for(cached[0]).tobytes()
            engine.graft_page_content(original, cached, 0)
            result = self.root/'grafted.pdf'
            original.save(result)
        with engine.open_pdf(result) as checked:
            self.assertEqual(len(checked), 2)
            self.assertEqual(engine.image_for(checked[0]).tobytes(), before)
            self.assertEqual(checked[0].get_links()[0]['page'], 1)
            self.assertEqual(checked.get_toc(), [[1, 'Second page', 2]])

    def test_fill_map_validates_coverage_and_preserves_review_gate(self):
        template = engine.read_json(self.job/'plan.template.json')
        mapping = {e['refs'][0]: '译文' for e in template['edits']}
        path = self.root/'translations.json'
        engine.write_json(path, mapping)
        args = argparse.Namespace(job=self.job, translations=path, reviewed=False, overwrite=False)
        with contextlib.redirect_stdout(StringIO()):
            engine.fill_plan(args)
        filled = engine.read_json(self.job/'plan.json')
        self.assertTrue(filled['review_required'])
        self.assertEqual(filled['keep'], template['keep'])
        self.assertEqual(filled['edits'][0]['box'], template['edits'][0]['box'])
        with self.assertRaisesRegex(ValueError, 'already contains'):
            engine.fill_plan(args)
        mapping['unexpected'] = '多余译文'
        engine.write_json(path, mapping)
        args.overwrite = True
        with self.assertRaisesRegex(ValueError, 'exactly match'):
            engine.fill_plan(args)

    def test_complete_translation_and_side_by_side_pages(self):
        self.apply()
        qa = engine.read_json(self.job/'qa.json')
        self.assertEqual(qa['automatic_checks'], 'pass')
        self.assertEqual(qa['visual_review'], 'pending_agent_review')
        with engine.open_pdf(self.output/'source_zh-CN.pdf') as translated:
            self.assertIn('学习目标', translated[0].get_text())
            self.assertIn('E(X) = 2', translated[0].get_text())
            self.assertNotIn('Learning objectives', translated[0].get_text())
        with engine.open_pdf(self.output/'source_dual.pdf') as paired:
            self.assertEqual(len(paired), 2)
            self.assertEqual(paired[0].rect.width, 1000)
            self.assertIn('Learning objectives', paired[0].get_text())
            self.assertIn('学习目标', paired[0].get_text())
            self.assertIn('Next slide', paired[1].get_text())
            self.assertIn('下一页', paired[1].get_text())

    def test_legacy_interleaved_mode_remains_available(self):
        self.apply(mode='interleaved', preview='none')
        with engine.open_pdf(self.output/'source_original-zh-CN.pdf') as paired:
            self.assertEqual(len(paired), 4)
            self.assertIn('Learning objectives', paired[0].get_text())
            self.assertIn('学习目标', paired[1].get_text())

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

    def test_side_by_side_count_mismatch(self):
        single = engine.fitz.open()
        single.new_page(width=500, height=400)
        with engine.open_pdf(self.source) as source:
            with self.assertRaisesRegex(ValueError, 'equal page counts'):
                engine.make_side_by_side(source, single, self.root/'bad-dual.pdf')
        single.close()

    def test_mixed_formula_and_footer_require_translation_review(self):
        formula = {'text': '(𝑋−μ)² and S', 'spans': [{'text': '(𝑋−μ)² and S', 'font': 'CambriaMath'}]}
        self.assertEqual(math_hint(formula)['suggested_action'], 'translate')
        formula['text'] = '~ N(0,1) as n→∞'
        formula['spans'][0]['text'] = formula['text']
        self.assertEqual(math_hint(formula)['suggested_action'], 'translate')
        doc = engine.fitz.open()
        page = doc.new_page(width=500, height=400)
        page.insert_text((20, 385), 'Copyright 2026 Author Slide 1 of 1', fontsize=10)
        info = engine.layout(doc)[0]
        engine.enrich_page(page, info)
        block = info['blocks'][0]
        self.assertEqual(block['classification'], 'footer')
        self.assertEqual(block['suggested_action'], 'translate')
        doc.close()

    def test_content_audit_reports_prose_kept_with_formula(self):
        manifest = {'pages': [{'rect': [0, 0, 500, 400], 'images': [], 'blocks': [
            {'id': 'p001.b000', 'text': 'x and y', 'classification': 'prose_with_math', 'spans': []},
            {'id': 'p001.b001', 'text': 'B. Author, 2026', 'classification': 'text', 'spans': []}]}]}
        plan = {'keep': [{'refs': ['p001.b000'], 'reason': 'formula'},
                         {'refs': ['p001.b001'], 'reason': 'citation'}]}
        with engine.open_pdf(self.source) as doc:
            warnings = engine.content_audit(manifest, plan, doc)
        self.assertEqual([w['code'] for w in warnings], ['prose_inside_keep'])

    def test_glossary_detects_missing_term_before_review(self):
        template = engine.read_json(self.job/'plan.template.json')
        mapping = {e['refs'][0]: '译文' for e in template['edits']}
        supplied = {'translations': mapping, 'glossary': [{'source': 'Learning objectives', 'target': '学习目标'}]}
        path = self.root/'translation-with-glossary.json'
        engine.write_json(path, supplied)
        args = argparse.Namespace(job=self.job, translations=path, reviewed=True, overwrite=False)
        with self.assertRaisesRegex(ValueError, 'glossary terms missing'):
            engine.fill_plan(args)
        warnings = engine.read_json(self.job/'terminology-warnings.json')
        self.assertEqual(warnings[0]['target'], '学习目标')

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
        brief = engine.read_json(self.job/'translation-brief.json')
        self.assertEqual(set(brief['translations']), {e['refs'][0] for e in draft['edits']})
        self.assertEqual(brief['source_sha256'], draft['source_sha256'])
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
