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
    # Short connectors such as "as" are missed by the 3+ letter word scan.
    # Keeping their entire block silently leaves prose untranslated.
    connectors = bool(re.search(r'\b(?:and|or|for|as|of|if|is|to|in|with|then)\b', normalized, re.I))
    prose = connectors or any(w.lower() not in {'var', 'cov', 'exp', 'log', 'sin', 'cos', 'tan', 'lim', 'max', 'min'} for w in words)
    has_cjk = any('\u3400' <= c <= '\u9fff' for c in normalized)
    operator = bool(re.search(r'[=<>∫∑√≤≥≠±∞]', normalized))
    strong = not prose and not has_cjk and (math_fonts > .45 or math_chars/count > .35 or
                                           (operator and bool(re.search('[A-Za-zα-ωΑ-Ω]', normalized))))
    return {'classification': 'math_formula' if strong else 'prose_with_math' if math_chars or math_fonts > .1 or connectors and operator else 'text',
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
            # Mixed copyright / author / slide-number lines need translation
            # decisions per span. Only number-only footers are safe to keep.
            numeric = bool(re.fullmatch(r'\s*\d+(?:\s*[/–-]\s*\d+)?\s*', text))
            block.update(classification='footer', suggested_action='keep' if numeric else 'translate',
                         review_note=None if numeric else 'Footer contains words; translate or split into reviewed spans.')
    associate_math(info['blocks'])


def associate_math(blocks):
    """Associate notation-only fragments with original strong seeds, never prose.

    No transitive propagation: proximity alone must not consume an entire slide.
    """
    seeds = [b for b in blocks if b.get('classification') == 'math_formula']
    for block in blocks:
        if block.get('suggested_action') == 'keep' or not block.get('horizontal', True):
            continue
        # Extraction can concatenate a styled variable with a roman function
        # (𝑋Var). Preserve that lexical boundary before NFKC turns it into XVar.
        separated = ''.join(' '+c+' ' if 0x1D400 <= ord(c) <= 0x1D7FF else c for c in block['text'])
        text = unicodedata.normalize('NFKC', separated).strip()
        words = re.findall(r'[A-Za-z]+', text)
        if not text or len(text) > 120 or any(len(w) > 1 and w.lower() not in
                {'var', 'cov', 'exp', 'log', 'sin', 'cos', 'tan', 'lim', 'max', 'min', 'dx', 'dy'} for w in words):
            continue
        if re.search(r'[^\sA-Za-z0-9α-ωΑ-Ω=+*/^().,{}\[\]<>|_−–∫∑√≤≥≠±∞∂-]', text):
            continue
        a = fitz.Rect(block['bbox'])
        size = max((s.get('size', 12) for s in block['spans']), default=12)
        related = []
        for seed in seeds:
            b = fitz.Rect(seed['bbox'])
            dx = max(a.x0-b.x1, b.x0-a.x1, 0)
            dy = max(a.y0-b.y1, b.y0-a.y1, 0)
            if (dy == 0 and dx <= size*.8) or (dx == 0 and dy <= size*.35):
                related.append(seed['id'])
        if related:
            block.update(classification='math_fragment', suggested_action='keep',
                         math_confidence=.7, math_context_refs=related)


def layout_recommendations(page, block, box, size):
    """Unapplied geometry proposals; apply's collision checks remain authoritative."""
    result = []
    others = [b for b in page['blocks'] if b['id'] != block['id']]
    obstacles = [b['bbox'] for b in others] + page['images']
    def clear(candidate, excluded=()):
        r = fitz.Rect(candidate)
        return (not r.is_empty and fitz.Rect(page['rect']).contains(r) and
                not any(r.intersects(fitz.Rect(b['bbox'])) for b in others if b['id'] not in excluded) and
                not any(r.intersects(fitz.Rect(im)) for im in page['images']))
    # Bounded right expansion preserves the existing left alignment.
    wider = list(box)
    wider[2] = min(page['rect'][2]-2, box[2]+max(24, size*3))
    for obstacle in obstacles:
        if obstacle[1] < wider[3] and obstacle[3] > wider[1] and obstacle[0] >= box[2]:
            wider[2] = min(wider[2], obstacle[0]-2)
    if wider[2] > box[2]+2 and clear(wider):
        result.append({'kind': 'widen_right', 'box': wider})
    for im in page['images']:
        if fitz.Rect(box).intersects(fitz.Rect(im)) and box[1] < im[1] < box[3]:
            # Source erasure is separate and must itself avoid the image.
            if not any(fitz.Rect(line).intersects(fitz.Rect(im)) for line in block.get('erase_rects', [block['bbox']])):
                clipped = [box[0], box[1], box[2], im[1]-2]
                if clipped[3]-clipped[1] >= size*1.5 and clear(clipped):
                    result.append({'kind': 'clamp_above_image', 'box': clipped})
    if re.search(r'\b(?:of|the|a|an|in|on|with|for|to|and|or)\s*$', block['text'], re.I):
        for other in others:
            b = other['bbox']
            if (other.get('suggested_action') != 'keep' and other.get('horizontal', True) and
                    abs(b[0]-box[0]) <= size*.4 and 0 <= b[1]-block['bbox'][3] <= size*.8 and
                    not re.match(r'\s*(?:[•●▪]|\d+[.)]|[A-Za-z][.)])', other['text'])):
                combined = list(fitz.Rect(block['bbox']) | fitz.Rect(b))
                if clear(combined, (other['id'],)):
                    result.append({'kind': 'merge_continuation', 'refs': [block['id'], other['id']], 'box': combined})
    return result


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
