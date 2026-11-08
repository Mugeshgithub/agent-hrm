# pdf_text.py: a tiny PDF writer for text pages (headings, paragraphs), with no dependency, so it also runs in CI.
# Used for the sample documents in the evals and, later, for the contract draft. Helvetica, Latin-1 accents and
# the euro sign (Windows-1252); anything else is replaced by "?". Lines are wrapped by character count.
PAGE_W, PAGE_H, MARGIN = 595, 842, 56
# style -> (font name, size, line height)
STYLES = {"h1": ("F2", 16, 24), "h2": ("F2", 12, 19), "p": ("F1", 10.5, 15), "b": ("F2", 10.5, 15), "small": ("F1", 9, 12.5)}


def _escape(text):
    raw = text.encode("cp1252", "replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _wrap(text, size):
    width = max(20, int((PAGE_W - 2 * MARGIN) / (size * 0.52)))
    lines = []
    for paragraph in text.split("\n"):
        words, current = paragraph.split(), ""
        for word in words:
            if current and len(current) + 1 + len(word) > width:
                lines.append(current)
                current = word
            else:
                current = f"{current} {word}".strip()
        lines.append(current)
    return lines


def make_pdf(blocks, title="Document"):
    """blocks: [(style, text)] with style h1, h2, p, b (bold), small or gap (text ignored). Returns the PDF bytes."""
    pages, ops, y = [], [], PAGE_H - MARGIN
    for style, text in blocks:
        if style == "gap":
            y -= 10
            continue
        font, size, leading = STYLES[style]
        for line in _wrap(text, size):
            if y < MARGIN + leading:
                pages.append(ops)
                ops, y = [], PAGE_H - MARGIN
            y -= leading
            if line:
                ops.append(b"BT /%s %s Tf %d %.1f Td (%s) Tj ET" % (font.encode(), repr(size).encode(), MARGIN, y, _escape(line)))
    pages.append(ops)

    objects = {1: b"<< /Type /Catalog /Pages 2 0 R >>", 3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
               4: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"}
    kids = []
    for i, page_ops in enumerate(pages):
        page_id, content_id = 5 + 2 * i, 6 + 2 * i
        stream = b"\n".join(page_ops)
        objects[content_id] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)
        objects[page_id] = (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
                            b"/Contents %d 0 R >>" % (PAGE_W, PAGE_H, content_id))
        kids.append(b"%d 0 R" % page_id)
    objects[2] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(kids), len(pages))
    info_id = max(objects) + 1
    objects[info_id] = b"<< /Title (%s) >>" % _escape(title)

    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += b"%d 0 obj\n%s\nendobj\n" % (number, objects[number])
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (info_id + 1)
    for number in range(1, info_id + 1):
        out += b"%010d 00000 n \n" % offsets[number]
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (info_id + 1, info_id, xref_at)
    return bytes(out)
