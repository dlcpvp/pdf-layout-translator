"""Conservative, reviewable layout hints. They never translate document content."""
from collections import Counter
import math
import re
import unicodedata

import pymupdf as fitz
from PIL import Image


def rgb_hex(rgb):
    return '#%02x%02x%02x' % tuple(max(0, min(255, round(c*255))) for c in rgb)


def background_hint(page, bbox, raster=None, drawings=None):
    """Corroborate vector fills with perimeter samples; abstain on mixed backgrounds."""
    if page.rotation:
        return {'suggested_fill': None, 'fill_confidence': 0, 'fill_method': 'rotated_page'}
    if raster is None:
        pix = page.get_pixmap(alpha=False)
        raster = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
    drawings = page.get_drawings() if drawings is None else drawings
    r = fitz.Rect(bbox)
    points = []
    for fraction in (.1, .25, .5, .75, .9):
        x = r.x0 + fraction*r.width
        y = r.y0 + fraction*r.height
        points += [(x, r.y0-1.5), (x, r.y1+1.5), (r.x0-1.5, y), (r.x1+1.5, y)]
    samples = [raster.getpixel((round(x), round(y))) for x, y in points
               if 0 <= round(x) < raster.width and 0 <= round(y) < raster.height]
    if not samples:
        return {'suggested_fill': None, 'fill_confidence': 0, 'fill_method': 'no_samples'}
    # Exact mode preserves a vector's actual RGB; a tolerance absorbs antialiasing.
    dominant = Counter(samples).most_common(1)[0][0]
    agreement = sum(max(abs(a-b) for a, b in zip(s, dominant)) <= 3 for s in samples)/len(samples)
    contained = [d for d in drawings if d.get('fill') is not None and
                 d.get('fill_opacity', 1) >= .99 and fitz.Rect(d['rect']).contains(r)]
    # Prefer latest containing paint, but trust it only if the visible pixels agree.
    if contained:
        candidate = contained[-1]['fill']
        candidate_rgb = tuple(round(c*255) for c in candidate)
        agreement_vector = sum(max(abs(a-b) for a, b in zip(s, candidate_rgb)) <= 3 for s in samples)/len(samples)
        if agreement_vector >= .85:
            return {'suggested_fill': rgb_hex(candidate), 'fill_confidence': round(agreement_vector, 3),
                    'fill_method': 'vector_and_pixels'}
    if agreement >= .9:
        return {'suggested_fill': '#%02x%02x%02x' % dominant,
                'fill_confidence': round(agreement, 3), 'fill_method': 'perimeter_pixels'}
    return {'suggested_fill': None, 'fill_confidence': round(agreement, 3), 'fill_method': 'mixed_background_review'}


def math_hint(block):
    text = block['text'].strip()
    normalized = unicodedata.normalize('NFKC', text)
    compact = ''.join(text.split())
    count = len(compact) or 1
    math_chars = sum(1 for c in compact if 0x1D400 <= ord(c) <= 0x1D7FF or
                     0x370 <= ord(c) <= 0x3FF or c in '∫∑Σ√≤≥≠±∞∈∉∩∪∏∂∇≈')
    math_fonts = sum(len(s['text'].strip()) for s in block['spans'] if
                     re.search(r'math|symbol|cmsy|cmmi|cmex', s['font'], re.I))/count
    words = re.findall(r'[A-Za-z]{3,}', normalized)
    prose = any(w.lower() not in {'var', 'cov', 'exp', 'log', 'sin', 'cos', 'tan', 'lim', 'for', 'and', 'or', 'max', 'min'} for w in words)
    has_cjk = any('\u3400' <= c <= '\u9fff' for c in normalized)
    operator = bool(re.search(r'[=<>∫∑√≤≥≠±∞]', normalized))
    strong = not prose and not has_cjk and (math_fonts > .45 or math_chars/count > .35 or
                                           (operator and bool(re.search('[A-Za-zα-ωΑ-Ω]', normalized))))
    return {'classification': 'math_formula' if strong else 'prose_with_math' if math_chars or math_fonts > .1 else 'text',
            'suggested_action': 'keep' if strong else 'translate',
            'math_confidence': .95 if strong and (operator or math_fonts > .8) else .8 if strong else .3 if math_chars else 0}


def enrich_page(page, info):
    pix = page.get_pixmap(alpha=False)
    raster = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
    drawings = page.get_drawings()
    height = page.rect.height
    for block in info['blocks']:
        block.update(background_hint(page, block['bbox'], raster, drawings))
        block.update(math_hint(block))
        text = block['text'].strip()
        footer = block['bbox'][1] > height*.9
        if footer and (re.search(r'©|copyright|all rights reserved', text, re.I) or
                       re.fullmatch(r'(?:slide\s*)?\d+(?:\s*(?:of|/)\s*\d+)?', text, re.I)):
            block.update(classification='footer', suggested_action='keep')


def typography_warnings(page, records):
    warnings = []
    page_spans = [s for b in page.get_text('dict')['blocks'] for line in b.get('lines', []) for s in line['spans']]
    closing = set('，。！？；：、）》】」』”’…,.!?;:%)]}')
    opening = set('（《【「『“‘([{')
    for rec in records:
        # Rejoin spans that belong to the same physical line before counting.
        lines = []
        for block in page.get_text('dict', clip=fitz.Rect(rec['box']))['blocks']:
            for line in block.get('lines', []):
                text = ''.join(s['text'] for s in line['spans']).strip()
                if text:
                    y = line['bbox'][1]
                    if lines and abs(lines[-1][0]-y) < rec.get('effective_font_size', rec['font_size'])*.35:
                        lines[-1] = (lines[-1][0], lines[-1][1]+text)
                    else:
                        lines.append((y, text))
        texts = [t for _, t in lines]
        def warn(code, detail):
            warnings.append({'page': rec['page'], 'refs': rec['refs'], 'code': code, 'detail': detail,
                             'suggestion': 'Inspect preview; try font_size -0.5pt or box +10pt only if collision checks allow. Do not omit text.'})
        if len(texts) > 1:
            last = ''.join(c for c in texts[-1] if not c.isspace() and not unicodedata.category(c).startswith('P'))
            if 0 < len(last) <= 2 and any('\u3400' <= c <= '\u9fff' for c in last):
                warn('orphan_character_detected', texts[-1])
        for (y, text) in lines:
            # An edited phrase can start after an untouched equation on the same line.
            # Its leading comma is not a physical line-start punctuation error.
            left_context = any(s['text'].strip() not in ('', '•', '·') and
                               0 <= rec['box'][0]-s['bbox'][2] <= rec['font_size']*1.5 and
                               abs(s['bbox'][1]-y) <= rec['font_size']*.5 for s in page_spans)
            if text[0] in closing and not left_context:
                warn('line_start_punctuation', text)
            if text[-1] in opening:
                warn('line_end_opening_punctuation', text)
    return warnings


def font_stats(doc):
    entries = []
    seen = set()
    for page in doc:
        for info in page.get_fonts(full=True):
            xref = info[0]
            if xref in seen:
                continue
            seen.add(xref)
            name, extension, _, data = doc.extract_font(xref)
            entries.append({'name': name, 'bytes': len(data), 'type': extension})
    return {'embedded_font_bytes': sum(x['bytes'] for x in entries),
            'largest_fonts': sorted(entries, key=lambda x: x['bytes'], reverse=True)[:5],
            'large_font_remaining': any(x['bytes'] > 2_000_000 for x in entries)}
