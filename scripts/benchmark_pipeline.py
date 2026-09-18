"""Measure synthetic layout iteration, not translation quality or agent latency.

Run with the skill's isolated Python. All PDFs stay in a temporary directory.
"""
import argparse
import contextlib
from io import StringIO
import json
from pathlib import Path
import tempfile

import pdf_translate as engine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pages', type=int, default=8)
    parser.add_argument('--font')
    parser.add_argument('--bold-font')
    args = parser.parse_args()
    if not 1 <= args.pages <= 100:
        parser.error('--pages must be between 1 and 100')
    with tempfile.TemporaryDirectory(prefix='pdf-layout-benchmark-') as temporary:
        root = Path(temporary)
        source, job = root/'report.pdf', root/'job'
        with engine.fitz.open() as doc:
            for number in range(args.pages):
                page = doc.new_page(width=595, height=842)
                page.insert_text((45, 60), f'Report section {number+1}', fontsize=22)
                for y in (130, 220, 310):
                    page.insert_text((45, y), 'This report describes observations and results.', fontsize=16)
            doc.save(source)
        with contextlib.redirect_stdout(StringIO()):
            engine.prepare(argparse.Namespace(input=source, job=job, target='zh-CN', no_preview=True))
        plan = engine.read_json(job/'plan.template.json')
        plan['review_required'] = False
        for edit in plan['edits']:
            edit['html'] = '报告的观察结果与分析。'
        engine.write_json(job/'plan.json', plan)
        for case, draft in [('cold_draft', True), ('warm_draft', True),
                            ('one_page_edit', True), ('final', False)]:
            if case == 'one_page_edit':
                plan['edits'][0]['html'] = '修订后的报告分析。'
                engine.write_json(job/'plan.json', plan)
            with contextlib.redirect_stdout(StringIO()) as captured:
                engine.apply(argparse.Namespace(job=job, out_dir=root/'out', font=args.font,
                             bold_font=args.bold_font, mode='translated', overwrite=True,
                             layout='auto', preview='none', no_cache=False, draft=draft))
            result = json.loads(captured.getvalue())
            print(json.dumps({'case': case, 'pages': args.pages, 'edits': len(plan['edits']),
                              'timings_seconds': result['timings_seconds'],
                              'reused_pages': result['reused_pages'], 'typeset_pages': result['typeset_pages'],
                              'output_bytes': Path(result['outputs'][0]).stat().st_size}), flush=True)


if __name__ == '__main__':
    main()
