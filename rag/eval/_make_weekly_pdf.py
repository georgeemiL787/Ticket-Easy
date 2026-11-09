"""Generate the 2-page A4 weekly progress PDF. Run from rag/: python eval/_make_weekly_pdf.py"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

pdfmetrics.registerFont(TTFont("Calibri", r"C:\Windows\Fonts\calibri.ttf"))
pdfmetrics.registerFont(TTFont("Calibri-Bold", r"C:\Windows\Fonts\calibrib.ttf"))

NAVY = HexColor("#1B365D")
NAVY_DARK = HexColor("#0E2340")
TEAL = HexColor("#0F7B7B")
TEAL_SOFT = HexColor("#E7F3F3")
GREY = HexColor("#F4F6F8")
LINE = HexColor("#D5DCE6")
TEXT = HexColor("#1A2332")
MUTED = HexColor("#5C6778")
GREEN = HexColor("#1F7A4D")
GREEN_BG = HexColor("#E8F5EE")
AMBER = HexColor("#9A6700")
AMBER_BG = HexColor("#F8F1DE")
PAGE_W, PAGE_H = A4
MARGIN = 16 * mm
CONTENT_W = PAGE_W - 2 * MARGIN
OUT = Path(__file__).with_name("Ticket-Easy_Weekly_Progress_Report_26-31Oct2026.pdf")
DESKTOP = Path.home() / "Desktop" / OUT.name


def _set(c: canvas.Canvas, name: str, size: float, color: Color = TEXT) -> None:
    c.setFont(name, size)
    c.setFillColor(color)


def _wrap(c: canvas.Canvas, text: str, font: str, size: float, max_w: float) -> list[str]:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if c.stringWidth(trial, font, size) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _round_rect(c: canvas.Canvas, x, y, w, h, r, fill, stroke=None) -> None:
    c.setFillColor(fill)
    if stroke:
        c.setStrokeColor(stroke)
        c.setLineWidth(0.4)
    c.roundRect(x, y, w, h, r, fill=1, stroke=1 if stroke else 0)


def _header(c: canvas.Canvas, page: int) -> None:
    c.setFillColor(NAVY_DARK)
    c.rect(0, PAGE_H - 22 * mm, PAGE_W, 22 * mm, fill=1, stroke=0)
    c.setFillColor(TEAL)
    c.rect(0, PAGE_H - 23.2 * mm, PAGE_W, 1.2 * mm, fill=1, stroke=0)
    _set(c, "Calibri", 8, HexColor("#A9B8CC"))
    c.drawString(MARGIN, PAGE_H - 8 * mm, "TICKET-EASY  ·  TEAM A")
    c.drawRightString(PAGE_W - MARGIN, PAGE_H - 8 * mm, "CONFIDENTIAL  ·  INTERNAL")
    _set(c, "Calibri-Bold", 16, white)
    c.drawString(MARGIN, PAGE_H - 16.5 * mm, "Weekly Progress Report")
    _set(c, "Calibri", 9, HexColor("#C5D4E8"))
    c.drawRightString(PAGE_W - MARGIN, PAGE_H - 16.2 * mm, "RAG Evaluation  ·  26–31 October 2026")


def _footer(c: canvas.Canvas, page: int) -> None:
    c.setFillColor(LINE)
    c.rect(MARGIN, 11 * mm, CONTENT_W, 0.3, fill=1, stroke=0)
    _set(c, "Calibri", 8, MUTED)
    c.drawString(MARGIN, 6.5 * mm, "Branch cursor/deepeval-rag-eval  ·  Sunday review 2 November 2026")
    c.drawRightString(PAGE_W - MARGIN, 6.5 * mm, f"Page {page} of 2")


def _kpi(c: canvas.Canvas, x, y, w, h, value: str, label: str, sub: str) -> None:
    _round_rect(c, x, y, w, h, 3, GREY)
    c.setFillColor(TEAL)
    c.rect(x, y, 1.6 * mm, h, fill=1, stroke=0)
    _set(c, "Calibri-Bold", 16, NAVY)
    c.drawString(x + 4 * mm, y + h - 8.2 * mm, value)
    _set(c, "Calibri-Bold", 7.5, TEAL)
    c.drawString(x + 4 * mm, y + h - 12.4 * mm, label.upper())
    _set(c, "Calibri", 7.4, MUTED)
    c.drawString(x + 4 * mm, y + 3.6 * mm, sub)


def _badge(c: canvas.Canvas, x, y, text: str, fg, bg) -> float:
    w = c.stringWidth(text, "Calibri-Bold", 7.5) + 6 * mm
    _round_rect(c, x, y, w, 5.2 * mm, 2.2, bg)
    _set(c, "Calibri-Bold", 7.5, fg)
    c.drawString(x + 3 * mm, y + 1.5 * mm, text)
    return w


def _section(c: canvas.Canvas, y: float, title: str) -> float:
    _set(c, "Calibri-Bold", 10.5, NAVY)
    c.drawString(MARGIN, y, title.upper())
    c.setStrokeColor(TEAL)
    c.setLineWidth(1.1)
    tw = c.stringWidth(title.upper(), "Calibri-Bold", 10.5)
    c.line(MARGIN, y - 1.6 * mm, MARGIN + tw, y - 1.6 * mm)
    return y - 7 * mm


def _bullets(c: canvas.Canvas, y: float, items: list[str], size: float = 8.4, leading: float = 3.7 * mm) -> float:
    for item in items:
        lines = _wrap(c, item, "Calibri", size, CONTENT_W - 5 * mm)
        c.setFillColor(TEAL)
        c.circle(MARGIN + 1.4 * mm, y + 1.1 * mm, 1.05 * mm, fill=1, stroke=0)
        _set(c, "Calibri", size, TEXT)
        for i, line in enumerate(lines):
            c.drawString(MARGIN + 5 * mm, y - i * leading * 0.85, line)
        y -= leading * (0.55 + 0.85 * len(lines))
    return y


def _table(c: canvas.Canvas, x, y, col_w: list[float], headers: list[str],
           rows: list[list[str]], row_h: float = 7.6 * mm, header_h: float = 6.8 * mm) -> float:
    total = sum(col_w)
    # header
    c.setFillColor(NAVY)
    c.rect(x, y - header_h, total, header_h, fill=1, stroke=0)
    _set(c, "Calibri-Bold", 7.2, white)
    cx = x
    for w, h in zip(col_w, headers):
        c.drawString(cx + 2.2 * mm, y - header_h + 2.2 * mm, h.upper())
        cx += w
    y -= header_h
    for ri, row in enumerate(rows):
        bg = GREY if ri % 2 == 0 else white
        c.setFillColor(bg)
        c.rect(x, y - row_h, total, row_h, fill=1, stroke=0)
        c.setStrokeColor(LINE)
        c.setLineWidth(0.25)
        c.line(x, y - row_h, x + total, y - row_h)
        cx = x
        for ci, (w, cell) in enumerate(zip(col_w, row)):
            if ci == 1:
                status = cell.lower()
                if status == "completed":
                    fg, sbg = GREEN, GREEN_BG
                elif status == "pending":
                    fg, sbg = AMBER, AMBER_BG
                else:
                    fg, sbg = MUTED, GREY
                bw = c.stringWidth(cell, "Calibri-Bold", 7) + 4.4 * mm
                _round_rect(c, cx + 2 * mm, y - row_h + 1.8 * mm, bw, 4.2 * mm, 1.8, sbg)
                _set(c, "Calibri-Bold", 7, fg)
                c.drawString(cx + 4 * mm, y - row_h + 2.9 * mm, cell)
            else:
                font = "Calibri-Bold" if ci == 0 else "Calibri"
                size = 7.4 if ci == 0 else 7.3
                _set(c, font, size, TEXT)
                max_w = w - 4.2 * mm
                lines = _wrap(c, cell, font, size, max_w)
                # vertically centre up to 2 lines
                if len(lines) == 1:
                    c.drawString(cx + 2.2 * mm, y - row_h + 2.7 * mm, lines[0])
                else:
                    c.drawString(cx + 2.2 * mm, y - row_h + 4.4 * mm, lines[0])
                    c.drawString(cx + 2.2 * mm, y - row_h + 1.5 * mm, lines[1])
            cx += w
        y -= row_h
    c.setStrokeColor(NAVY)
    c.setLineWidth(0.6)
    c.rect(x, y, total, header_h + row_h * len(rows), fill=0, stroke=1)
    return y


def page1(c: canvas.Canvas) -> None:
    _header(c, 1)
    y = PAGE_H - 28 * mm

    _round_rect(c, MARGIN, y - 9.5 * mm, CONTENT_W, 9.5 * mm, 2.5, TEAL_SOFT)
    _badge(c, MARGIN + 3 * mm, y - 7.2 * mm, "ON TRACK", GREEN, GREEN_BG)
    _set(c, "Calibri", 8.4, TEXT)
    c.drawString(MARGIN + 28 * mm, y - 5.8 * mm,
                 "Week scope complete. Hybrid re-run and half-true / telco experiments finished. Merge to main is pending.")
    y -= 14 * mm

    gap = 3.2 * mm
    kw = (CONTENT_W - 3 * gap) / 4
    kh = 18 * mm
    cards = [
        ("48/54", "Judge agreement", "qwen3:8b  ·  54 labels  ·  ≥ 0.5"),
        ("89%", "Shop section hit", "Hybrid bge-m3  ·  was 82%"),
        ("0%", "Shop off-topic", "Want low  ·  was 17% keyword"),
        ("3", "Tenants scored", "shop · noon · telco hybrid"),
    ]
    for i, (v, lab, sub) in enumerate(cards):
        _kpi(c, MARGIN + i * (kw + gap), y - kh, kw, kh, v, lab, sub)
    y -= kh + 8 * mm

    y = _section(c, y, "Executive summary")
    y = _bullets(c, y, [
        "Built a DeepEval RAG eval next to existing retrieval tests: passage relevancy and reply faithfulness, gold as policy section IDs, judges local Ollama or Groq only.",
        "Trusted judge is local qwen3:8b at 48/54 (89%) on 54 hand labels. Quote 48/54, not the earlier 30/30. Groq 43/54; 4b 46/54. Live quoted replies at 91% are inflated.",
        "Hybrid bge-m3 re-run is done: shop 89% / off-topic 0%; noon 98%; telco 82% but off-topic still ~44% and no-answer recall fell 58% → 33%.",
        "Tried stricter faithfulness and a telco score floor; both hurt held-out quality and were not shipped.",
    ], size=8.5, leading=4.05 * mm)
    y -= 1.5 * mm

    y = _section(c, y, "Key accomplishments")
    y = _bullets(c, y, [
        "CLI python -m team_a eval-rag (section hit, abstention, relevancy, faithfulness) plus 8 unit tests.",
        "54-row faithfulness exam: original 30 untouched, 24 appended (wrong number, wrong section, invented extra, half-true). Hand-reviewed; all labels kept.",
        "Judge bake-off, Arabizi→Arabic script (17/22 → 18/22, not a fix), RAGAS-prompt fallback 47/54 with 2 false alarms.",
        "Pulled bge-m3, rebuilt shop / noon / telco indexes, re-scored hybrid relevancy on the test splits.",
    ], size=8.4, leading=3.95 * mm)
    y -= 1.5 * mm

    y = _section(c, y, "Milestones this week")
    y = _bullets(c, y, [
        "Framework selected: DeepEval default; RAGAS fallback; TruLens / Phoenix / promptfoo deferred.",
        "Work is on branch cursor/deepeval-rag-eval (pushed). Pull request into main is not open yet.",
    ], size=8.4, leading=3.95 * mm)
    y -= 2 * mm

    y = _section(c, y, "Locked decisions")
    locked = [
        ("01", "Trusted judge is local qwen3:8b. Not Groq, not 4b."),
        ("02", "Quote 48/54 at ≥ 0.5. Do not quote the earlier 30/30."),
        ("03", "Hybrid bge-m3 is how relevancy is scored from now on."),
        ("04", "Telco off-topic is retrieval / corpus, not a DeepEval bug."),
        ("05", "91% live-quote faithfulness is not “the bot is grounded.” Use the 54 labels."),
    ]
    box_h = 8 * mm + 6.6 * mm * len(locked)
    _round_rect(c, MARGIN, y - box_h, CONTENT_W, box_h, 3, GREY)
    c.setFillColor(TEAL)
    c.rect(MARGIN, y - box_h, 1.6 * mm, box_h, fill=1, stroke=0)
    for i, (num, text) in enumerate(locked):
        ty = y - 7.2 * mm - i * 6.6 * mm
        _set(c, "Calibri-Bold", 8, TEAL)
        c.drawString(MARGIN + 5 * mm, ty, num)
        _set(c, "Calibri", 8.3, TEXT)
        c.drawString(MARGIN + 13 * mm, ty, text)
    _footer(c, 1)


def page2(c: canvas.Canvas) -> None:
    _header(c, 2)
    y = PAGE_H - 28 * mm
    y = _section(c, y, "Progress by workstream")
    y = _table(
        c, MARGIN, y,
        [48 * mm, 24 * mm, 22 * mm, CONTENT_W - 94 * mm],
        ["Task / workstream", "Status", "Progress", "Key update"],
        [
            ["DeepEval harness + CLI", "Completed", "Done", "eval-rag next to eval-retrieval; does not replace recall@5"],
            ["54-row faithfulness labels", "Completed", "54/54", "30 original + 24 new; hand-reviewed; all kept"],
            ["Judge comparison", "Completed", "48/54", "qwen3:8b 89%; Groq 80%; 4b 85%; 0 false alarms at ≥ 0.5"],
            ["Hybrid relevancy (bge-m3)", "Completed", "3/3", "Shop 89% / 0% off-topic; noon 98%; telco 82% / 44%"],
            ["Half-true scoring experiment", "Completed", "Tried", "Any-claim-fails: 41/54. Keep ≥ 0.5; strict score is diagnostic only"],
            ["Telco confidence floor", "Completed", "Tried", "0.50 helped dev; test Arabizi 19/30 → 15/30. Not shipped"],
            ["PR into main", "Pending", "Not started", "Branch pushed; pull request not opened"],
            ["Telco off-topic product rule", "Pending", "Open", "Jobs / 5G / TV / insurance still retrieve; needs when-to-return-empty"],
            ["Conversation citation 70.7% vs 95%", "Pending", "Open", "Agent not quoting; retrieval already high. Team B / prompt"],
        ],
        row_h=8.3 * mm,
        header_h=6.4 * mm,
    )
    y -= 7 * mm

    y = _section(c, y, "Relevancy — keyword vs hybrid")
    y = _table(
        c, MARGIN, y,
        [32 * mm, 42 * mm, 42 * mm, CONTENT_W - 116 * mm],
        ["Tenant", "Section hit", "Off-topic still relevant", "Note"],
        [
            ["shop_001", "82% → 89%", "17% → 0%", "Clean win on hybrid"],
            ["noon_eg", "90% → 98%", "14% → 18%", "Recall up; off-topic slightly worse"],
            ["telco_001", "77% → 82%", "46% → 44%", "No-answer recall 58% → 33%"],
        ],
        row_h=7.4 * mm,
        header_h=6.2 * mm,
    )
    y -= 7 * mm

    y = _section(c, y, "Tried and not shipped")
    y = _bullets(c, y, [
        "Arabizi to Arabic script: 17/22 → 18/22. Optional preprocess, not a fix.",
        "RAGAS as default: stricter on Arabizi errors, 2 false alarms on good paraphrases. Fallback only.",
        "Faithfulness “any unsupported claim fails”: 48/54 → 41/54. Judge reads “14 days” as “1:4”. Rows scored 1.00 still slip.",
        "Telco min_top_score 0.50: better abstention on dev; worse Arabizi on held-out test. Floor not shipped.",
    ], size=8.2, leading=3.75 * mm)
    y -= 1.2 * mm

    y = _section(c, y, "Next week  ·  management asks")
    y = _bullets(c, y, [
        "If merged: open the PR; telco product rule (when to return empty — not another judge); optional CI for deterministic eval-rag only.",
        "Conversation citation 70.7% vs 95% stays outside this RAG-eval track unless management assigns it.",
    ], size=8.2, leading=3.75 * mm)
    y -= 2.5 * mm
    box_h = 28 * mm
    _round_rect(c, MARGIN, y - box_h, CONTENT_W, box_h, 3, NAVY)
    _set(c, "Calibri-Bold", 8, HexColor("#9ED9D4"))
    c.drawString(MARGIN + 4 * mm, y - 6.2 * mm, "SUNDAY DECISION PACK")
    asks = [
        "1.  Merge cursor/deepeval-rag-eval into main?",
        "2.  Who owns telco “return empty on off-topic”?",
        "3.  Is conversation citation 70.7% vs 95% this track?",
    ]
    _set(c, "Calibri", 8.6, white)
    for i, a in enumerate(asks):
        c.drawString(MARGIN + 4 * mm, y - 12.4 * mm - i * 5.2 * mm, a)
    _footer(c, 2)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(OUT), pagesize=A4)
    c.setTitle("Ticket-Easy Weekly Progress Report — RAG Evaluation — 26–31 October 2026")
    c.setAuthor("Team A")
    c.setSubject("Weekly progress report for Sunday review 2 November 2026")
    page1(c)
    c.showPage()
    page2(c)
    c.save()
    DESKTOP.write_bytes(OUT.read_bytes())
    print(f"wrote {OUT}")
    print(f"wrote {DESKTOP}")


if __name__ == "__main__":
    main()
