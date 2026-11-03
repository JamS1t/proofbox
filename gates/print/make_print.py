#!/usr/bin/env python3
"""Filming-day print files from gates/answer_key.csv and gates/orders/*_truth.json:
labels.pdf (8 waybill labels), filming.pdf (checklist), FILMING.md (repo root), and 600-dpi page images
in gates/print/pages/ for print_long.ps1. Paper: Long bond, 8.5 x 13 in.

    python gates/print/make_print.py
"""
import csv
import json
from pathlib import Path

import fitz
from PIL import Image
from reportlab.graphics.barcode.code128 import Code128
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

HERE = Path(__file__).resolve().parent
GATES = HERE.parent
LONG = (8.5 * inch, 13 * inch)
LABELS = ["TUB2JN", "TUB2JN", "TUB2JX", "TUB2JU", "6DVME7", "5EECYY", "KQWMTM", ""]  # 2nd TUB2JN = spare for U9
HEADER = ("Say the clip ID out loud first. Label to camera 2 s. Show all box sides before opening. Hold each item 2 s "
          "with its printed text facing the camera. Sellers: tape the box and end on a close-up. "
          "Landscape, 1080p, under 60 s.")
# Second takes and demo shots: (clip, what changes). Filmed right after their base clip; demos at the end.
EXTRA = {"G1": [("G1_b", "Second take: phone in PORTRAIT")], "G2": [("G2_b", "Second take: DIM light")],
         "U2": [("U2_b", "Second take: phone in PORTRAIT")], "U3": [("U3_b", "Second take: DIM light")],
         "P2": [("P2_b", "Second take: phone in PORTRAIT")], "P5": [("P5_b", "Second take: DIM light")]}
DEMOS = [("DEMO_U2", "U2", "Demo shot: U2 again in your BEST light"), ("DEMO_G2", "G2", "Demo shot: G2 again in your BEST light")]
WORDS = dict(zip("123456789", "one two three four five six seven eight nine".split()))


def spoken(clip):
    """G1_b -> 'clip G one B', G3a -> 'clip G three A', DEMO_U2 -> 'clip demo U two'."""
    demo, base = clip.startswith("DEMO_"), clip.removeprefix("DEMO_")
    s = f"{base[0]} {WORDS[base[1]]}" + (" " + base[2:].strip("_").upper() if base[2:] else "")
    return "clip " + ("demo " if demo else "") + s


def filming_rows():
    key = list(csv.DictReader(open(GATES / "answer_key.csv", encoding="utf-8")))
    rows = []
    for r in key:
        if r["file"] == "G3":
            rows += [dict(r, file="G3a", do="Recording 1 of 2: sides, label, open the box. Then STOP."),
                     dict(r, file="G3b", do="Recording 2 of 2: take the items out.")]
        else:
            rows.append(r)
        rows += [dict(r, file=c, do=d) for c, d in EXTRA.get(r["file"], [])]
    by = {r["file"]: r for r in key}
    rows += [dict(by[b], file=c, do=d) for c, b, d in DEMOS]
    return rows


def labels(path):
    truth = {json.load(open(p, encoding="utf-8"))["label_text"]: json.load(open(p, encoding="utf-8"))["order_id"]
             for p in (GATES / "orders").glob("*_truth.json")}
    c = canvas.Canvas(str(path), pagesize=LONG)
    m, cols, rows = 0.3 * inch, 2, 4
    cw, ch = (LONG[0] - 2 * m) / cols, (LONG[1] - 2 * m) / rows
    c.setDash(6, 4)
    c.setLineWidth(0.8)
    for i in range(cols + 1):
        c.line(m + i * cw, 0, m + i * cw, LONG[1])
    for j in range(rows + 1):
        c.line(0, m + j * ch, LONG[0], m + j * ch)
    c.setDash()
    for n, code in enumerate(LABELS):
        x, y = m + (n % cols) * cw + 14, m + (rows - 1 - n // cols) * ch + 14  # cell origin + padding
        w, h = cw - 28, ch - 28
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x, y + h - 10, "MARKETPLACE EXPRESS")
        c.drawRightString(x + w, y + h - 10, "WAYBILL")
        c.setLineWidth(1.2)
        c.line(x, y + h - 16, x + w, y + h - 16)
        if not code:
            continue
        head, last = code[:-1], code[-1]
        size = min(80, w / (stringWidth(head, "Helvetica", 1) + stringWidth(last, "Helvetica-Bold", 1)))
        tw = stringWidth(head, "Helvetica", size) + stringWidth(last, "Helvetica-Bold", size)
        bx, by = x + (w - tw) / 2, y + h - 30 - size * 0.75
        c.setFont("Helvetica", size)
        c.drawString(bx, by, head)
        lx = bx + stringWidth(head, "Helvetica", size)
        c.setFont("Helvetica-Bold", size)
        c.drawString(lx, by, last)
        c.setLineWidth(size / 18)
        c.line(lx, by - size * 0.1, lx + stringWidth(last, "Helvetica-Bold", size), by - size * 0.1)
        bc = Code128(truth[code], barHeight=0.6 * inch, barWidth=1.0, quiet=False)
        bc.barWidth = min(1.6, (w - 20) / bc.width)  # widest bars that fit
        bc = Code128(truth[code], barHeight=0.6 * inch, barWidth=bc.barWidth, quiet=False)
        bc.drawOn(c, x + (w - bc.width) / 2, y + 30)
        c.setFont("Helvetica-Bold", 14)
        c.drawCentredString(x + w / 2, y + 6, "Juan D. / Tacloban City")
    c.save()


class Box(Flowable):
    """An empty checkbox."""
    def __init__(self, size=18):
        super().__init__()
        self.size = self.width = self.height = size

    def draw(self):
        self.canv.setLineWidth(1.6)
        self.canv.rect(0, 0, self.size, self.size)


def filming(path, rows):
    page = (LONG[1], LONG[0])  # landscape Long
    body = ParagraphStyle("b", fontName="Helvetica", fontSize=14, leading=17)
    bold = ParagraphStyle("h", parent=body, fontName="Helvetica-Bold")
    small = ParagraphStyle("s", parent=body, fontSize=10, leading=12)
    data = [[""] + [Paragraph(t, bold) for t in ("ID (say it)", "Label", "In the box", "What I do", "Expected")]]
    for r in rows:
        data.append([Box(), [Paragraph(r["file"], bold), Paragraph(spoken(r["file"]), small)],
                     Paragraph(r["label"], bold), Paragraph(r["in_box"], body), Paragraph(r["do"], body),
                     Paragraph(r["expected"], body)])
    widths = [0.45, 1.35, 1.0, 2.9, 4.3, 2.4]
    t = Table(data, colWidths=[w * inch for w in widths], repeatRows=1)
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.6, "#555555"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("BACKGROUND", (0, 0), (-1, 0), "#DDDDDD"), ("TOPPADDING", (0, 0), (-1, -1), 5),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    doc = SimpleDocTemplate(str(path), pagesize=page, leftMargin=0.3 * inch, rightMargin=0.3 * inch,
                            topMargin=0.3 * inch, bottomMargin=0.3 * inch, title="ProofBox filming checklist")
    doc.build([Paragraph(HEADER, ParagraphStyle("t", parent=bold, fontSize=16, leading=20)), Spacer(1, 8), t])


def filming_md(rows):
    md = ["# Filming checklist", "", HEADER, "",
          "Labels: print `gates/print/labels.pdf` (TUB2JN, TUB2JN spare for U9, TUB2JX, TUB2JU, 6DVME7, 5EECYY, KQWMTM, blank).",
          "Clips go in `gates/clips/raw/` in filming order. Say the spoken ID in the first 3 s.", "",
          "| Done | ID | Say | Label | In the box | What I do | Expected |", "|---|---|---|---|---|---|---|"]
    md += [f"| [ ] | {r['file']} | {spoken(r['file'])} | {r['label']} | {r['in_box']} | {r['do']} | {r['expected']} |"
           for r in rows]
    (GATES.parent / "FILMING.md").write_text("\n".join(md) + "\n", encoding="utf-8")


def page_images(pdf, rotate=False):
    """600-dpi 1-bit page images (landscape pages turned to portrait so they print on the Long sheet)."""
    out = []
    (HERE / "pages").mkdir(exist_ok=True)
    for i, page in enumerate(fitz.open(pdf)):
        pix = page.get_pixmap(dpi=600, colorspace=fitz.csGRAY)
        im = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        if rotate:
            im = im.rotate(90, expand=True)
        p = HERE / "pages" / f"{pdf.stem}_{i + 1}.png"
        im.point(lambda v: 255 if v > 150 else 0).convert("1").save(p, dpi=(600, 600))
        out.append(p)
    return out


if __name__ == "__main__":
    rows = filming_rows()
    labels(HERE / "labels.pdf")
    filming(HERE / "filming.pdf", rows)
    filming_md(rows)
    for pdf, rot in ((HERE / "labels.pdf", False), (HERE / "filming.pdf", True)):
        print(pdf.name, [p.name for p in page_images(pdf, rot)])
    print(f"{len(rows)} filming rows")
