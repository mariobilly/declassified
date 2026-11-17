"""Procedural document pages: court filings, typewriter memos, sealed and privacy sheets.

Everything is laid out in *design units*: the page is 1000 units tall and PAGE_W units wide, and text sits on
the same line grid (rows of height L, starting at `phase`) that the redaction bars use later. A layout is
resolution independent; render_ink() rasterises it at any pixel scale.
"""
import functools
import os
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFont

PAGE_H = 1000.0
PAGE_W = 810.0           # 0.81 aspect, measured on the reel (810 x 1000 px page on a 1080 x 1920 frame)
MARGIN_L = 0.05 * PAGE_W
MARGIN_R = 0.95 * PAGE_W

STYLES = ("court", "memo", "sealed", "privacy")

FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
FONT_FILES = {
    "times": "times.ttf", "times_b": "timesbd.ttf",
    "cour": "cour.ttf", "cour_b": "courbd.ttf",
    "arial": "arial.ttf", "arial_b": "arialbd.ttf",
}
# Font size as a fraction of the line height, per font family.
FONT_SCALE = {"times": 1.0, "times_b": 1.0, "cour": 1.0, "cour_b": 1.0, "arial": 0.78, "arial_b": 0.78}
# Right edge of the body text per style, as a fraction of the page width (measured on the reel: the typed
# column stops mid-page, which keeps the face clean).
COLUMN_R = {"court": 0.48, "memo": 0.63}


@functools.lru_cache(maxsize=256)
def font(key, px):
    px = max(4, int(round(px)))
    try:
        return ImageFont.truetype(os.path.join(FONT_DIR, FONT_FILES[key]), px)
    except OSError:
        return ImageFont.load_default()


@functools.lru_cache(maxsize=4096)
def text_width(key, size, text):
    """Width in design units of `text` set at `size` design units."""
    # Measure at 4x so the rounding of tiny font sizes doesn't skew the wrap.
    return font(key, size * 4).getlength(text) / 4.0


# --------------------------------------------------------------------------------------------- word banks
SURNAMES = ["Ingram", "Langley", "Whitfield", "Coleman", "Marshall", "Foster", "Simmons", "Kendrick", "Holloway",
            "Pryor", "Sutton", "Barlow", "Keane", "Mercer", "Ashby", "Talbot", "Whitaker", "Dunmore", "Corrigan",
            "Hale", "Brennan", "Vance", "Lockhart", "Ellison", "Prescott", "Rourke", "Stanton", "Calloway"]
FIRSTNAMES = ["Alex", "Nathan", "Ruth", "Daniel", "Mara", "Henry", "Iris", "Owen", "Clara", "Victor", "June",
              "Elliot", "Sofia", "Martin", "Leah", "Thomas", "Vera", "Simon", "Nora", "Felix", "Ada", "Jonah"]
MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER",
          "NOVEMBER", "DECEMBER"]
CLASSIFICATIONS = ["SECRET", "TOP SECRET", "CONFIDENTIAL", "SECRET", "TOP SECRET", "CONFIDENTIAL/LIMDIS",
                   "SECRET/NOFORN", "TOP SECRET - EYES ONLY"]
OFFICES = ["OFFICE OF SPECIAL PLANS", "DEPARTMENT OF GENERAL SERVICES", "BUREAU OF TECHNICAL SERVICES",
           "OFFICE OF THE COORDINATOR", "DIVISION OF FIELD OPERATIONS", "JOINT LOGISTICS REVIEW BOARD",
           "DIVISION OF PUBLIC INFORMATION", "OFFICE OF RECORDS MANAGEMENT", "BUREAU OF INTERNAL REVIEW"]
ADDRESSEES = ["EXECUTIVE SECRETARY, REVIEW BOARD", "DIRECTOR, FIELD OPERATIONS", "CHIEF, RECORDS DIVISION",
              "DEPUTY COORDINATOR", "ALL STATION CHIEFS", "CHAIRMAN, REVIEW COMMITTEE", "ASSISTANT DIRECTOR (PLANS)"]
SUBJECTS = ["WEEKLY ACTIVITY SUMMARY", "DISPOSITION OF RECORDS", "REQUEST FOR CLARIFICATION",
            "STATUS OF PROJECT REVIEW", "TRANSFER OF MATERIALS", "INTERIM REPORT", "PERSONNEL SECURITY REVIEW",
            "PROPOSED REORGANIZATION", "RESULTS OF INQUIRY"]
SIGNERS = ["Executive Officer", "Chief, Records Division", "Deputy Director", "Acting Coordinator",
           "Security Officer", "Review Board Secretary"]
FOOTERS = ["DECLASSIFIED IN PART - E.O. 13526", "DECLASSIFIED IN PART - E.O. 13526",
           "APPROVED FOR RELEASE IN PART", "SANITIZED COPY APPROVED FOR RELEASE", "DECLASSIFIED - AUTHORITY NND 917"]
HEADINGS = ["I.  DISCUSSION", "II.  CONCLUSION", "III.  ANALYSIS", "STATEMENT OF FACTS", "BACKGROUND",
            "LEGAL STANDARD", "RELIEF REQUESTED", "II.  DISCUSSION", "IV.  CONCLUSION", "PROCEDURAL HISTORY"]
STAMPS = ["DECLASSIFIED", "RECEIVED", "BEST COPY AVAILABLE", "DECLASSIFIED", "RECEIVED",
          "APPROVED FOR RELEASE", "COPY"]

COURT_SENTENCES = [
    "The materials produced in discovery on {date} were reviewed by counsel and are summarized in the margin.",
    "The photographic identification procedure used was the functional equivalent of a one-on-one show-up, and any identification so obtained is tainted and should therefore be suppressed.",
    "Any remaining objections were waived when no timely response was filed.",
    "The exhibits attached hereto are incorporated by reference as though fully set forth herein.",
    "The record contains no support for the proposition advanced by the defendant.",
    "This principle applies both to show-ups and to photographic identifications.",
    "Nothing in the accompanying declaration alters the analysis set forth above.",
    "The Court has broad discretion to fashion an appropriate remedy under the circumstances presented.",
    "Counsel for the parties conferred in good faith prior to the filing of the instant motion.",
    "The Government presented the witness with a series of photographs, attached hereto as Exhibit {exh}.",
    "A defendant's right to due process is violated where the identification procedure is so unnecessarily suggestive as to give rise to a very substantial likelihood of irreparable misidentification.",
    "For the foregoing reasons, the motion should be granted in its entirety.",
    "On {date_s}, the witness was shown a single photograph of the defendant and asked whether she recognized him.",
    "The declaration of the officer does not establish that the procedure was necessary.",
    "The parties dispute whether the materials were properly withheld under the applicable exemption.",
    "The Court declines to reach the remaining arguments in light of its ruling above.",
    "Defendant further contends that the search exceeded the scope of the warrant.",
    "The affidavit submitted in support of the application sets forth facts sufficient to establish probable cause.",
    "Plaintiff has not identified any genuine dispute of material fact that would preclude summary judgment.",
    "The redacted portions of the record concern matters of personal privacy and are properly withheld.",
    "See {cite}.",
    "See also {cite}.",
    "Accord {cite}.",
    "{cite}.",
]

MEMO_SENTENCES = [
    "No record of the individual named above could be located in the central index.",
    "Funds for the continuation of this activity have been identified within existing allocations.",
    "The foregoing is furnished for your information and retention.",
    "In accordance with the directive of {date}, all materials described in enclosure (1) have been transferred to secure storage.",
    "Pursuant to the review of {date}, the committee has reviewed the matter in its entirety and finds that no further action is warranted at this time.",
    "The originals remain in the custody of the issuing office pending final determination by the review board.",
    "Distribution of this memorandum is limited to the addressees of the basic instrument.",
    "The undersigned requests that the attached records be referred to the appropriate office for disposition.",
    "Receipt by the addressee is to be acknowledged in writing not later than {date}.",
    "It is recommended that the subject be advised of the findings at the earliest opportunity.",
    "A copy of this memorandum has been placed in the official file.",
    "Further inquiries regarding this matter should be directed to the undersigned.",
    "Reference is made to the memorandum of {date}, subject as above.",
    "The attached summary reflects all activity reported through the close of the period.",
    "Arrangements have been completed for the transfer of the remaining files to the records center.",
]


def _date(rng, upper=False):
    d = f"{rng.randint(1, 28)} {rng.choice(MONTHS)} {rng.randint(1952, 1979)}"
    return d if upper else d.title()


def _date_short(rng):
    return f"{rng.choice(MONTHS).title()} {rng.randint(1, 28)}, {rng.randint(1996, 2019)}"


def _cite(rng):
    a, b = rng.sample(SURNAMES, 2)
    kind = rng.random()
    if kind < 0.4:
        return f"{a} v. {b}, {rng.randint(300, 580)} U.S. {rng.randint(1, 900)}, {rng.randint(1, 900)} ({rng.randint(1955, 2015)})"
    if kind < 0.75:
        return (f"{a} v. {b}, {rng.randint(100, 999)} F.{rng.choice(['2d', '3d'])} {rng.randint(1, 1400)}, "
                f"{rng.randint(1, 1400)} ({rng.randint(1, 11)}th Cir. {rng.randint(1970, 2018)})")
    return f"United States v. {a}, {rng.randint(100, 999)} F. Supp. {rng.randint(1, 1400)} ({rng.randint(1970, 2018)})"


def _fill(rng, template):
    return template.format(date=_date(rng), date_s=_date_short(rng), cite=_cite(rng), exh=rng.choice("ABCDEFG"))


@functools.lru_cache(maxsize=20000)
def word_width(key, size, word):
    return text_width(key, size, word)


def _wrap(words, key, size, first_w, rest_w):
    """Greedy word wrap using cached per-word widths; returns line strings."""
    space = word_width(key, size, " ")
    lines, cur, cur_w, width = [], [], 0.0, first_w
    for w in words:
        ww = word_width(key, size, w)
        trial = cur_w + (space if cur else 0.0) + ww
        if trial <= width or not cur:
            cur.append(w)
            cur_w = trial
        else:
            lines.append(" ".join(cur))
            cur, cur_w, width = [w], ww, rest_w
    if cur:
        lines.append(" ".join(cur))
    return lines


# --------------------------------------------------------------------------------------------- layouts
class Item:
    """One text line: x in design units (left edge, or centre/right edge depending on align), grid row."""
    __slots__ = ("x", "row", "text", "font", "size", "align", "ink")

    def __init__(self, x, row, text, font_key, size, align="left", ink=1.0):
        self.x, self.row, self.text, self.font, self.size, self.align, self.ink = x, row, text, font_key, size, align, ink


class Layout:
    def __init__(self, style, L, phase, items, stamp=None, ink_rgb=(30, 30, 30), name_row=None):
        self.style, self.L, self.phase, self.items, self.stamp = style, L, phase, items, stamp
        self.ink_rgb, self.name_row = ink_rgb, name_row


def grid_rows(L, phase):
    return int((PAGE_H - phase) // L)


def _body(rng, items, sentences, key, size, rows_from, rows_to, L, right=MARGIN_R, numbered=False,
          text_amount=1.0, headings=None, indent=0.06 * PAGE_W, blank_p=0.35):
    """Fill grid rows [rows_from, rows_to) with wrapped paragraphs between MARGIN_L and `right`."""
    row, n = rows_from, 1
    width = right - MARGIN_L
    indent = min(indent, width * 0.15)
    pending_heads = list(headings or [])
    while row < rows_to:
        if pending_heads and rng.random() < 0.18 and row + 3 < rows_to:
            head = pending_heads.pop(0)
            items.append(Item(PAGE_W / 2, row + 1, head, key + "_b", size, "center"))
            row += 3
            continue
        k = rng.randint(2, 5)
        text = " ".join(_fill(rng, rng.choice(sentences)) for _ in range(k))
        if numbered:
            text = f"{n}. {text}"
            n += 1
        lines = _wrap(text.split(), key, size, width - (0 if numbered else indent), width)
        for i, ln in enumerate(lines):
            if row >= rows_to:
                break
            x = MARGIN_L + (indent if (i == 0 and not numbered) else 0)
            if rng.random() < text_amount:
                items.append(Item(x, row, ln, key, size))
            row += 1
        if rng.random() < blank_p:
            row += 1


def layout_page(style, seed, L, phase, text_width=1.0, stamp_p=0.5, content_top=106, text_amount=1.0):
    rng = random.Random(seed)
    rows = grid_rows(L, phase)
    right = {k: min(MARGIN_R, MARGIN_L + (v * PAGE_W - MARGIN_L) * text_width) for k, v in COLUMN_R.items()}
    items = []
    stamp = None
    ink = (30, 30, 30)
    name_row = None
    if style == "court":
        hs = L * FONT_SCALE["arial"] * 0.8
        hdr = (f"Case {rng.randint(1, 9)}:{rng.randint(10, 25)}-cv-{rng.randint(10000, 99999)}-"
               f"{''.join(rng.choice('ABCDEFGHJKLMNPRSTWXY') for _ in range(3))}"
               f"     Document {rng.randint(12, 999)}     Filed {rng.randint(1, 12)}/{rng.randint(1, 28)}/"
               f"{rng.randint(10, 24)}     Page {rng.randint(2, 9)} of {rng.randint(10, 40)}")
        items.append(Item(PAGE_W / 2, 1, hdr, "arial", hs, "center"))
        size = L * FONT_SCALE["times"]
        start = max(3, int(round(rng.uniform(0.045, 0.115) * PAGE_H / L)))
        heads = rng.sample(HEADINGS, rng.randint(1, 2))
        _body(rng, items, COURT_SENTENCES, "times", size, start, rows - 2, L, right=right["court"],
              text_amount=text_amount, headings=heads)
        ink = (24, 24, 26)
    elif style == "memo":
        size = L * FONT_SCALE["cour"]
        u = PAGE_H / L / 77.0                      # header rows were measured at 77 lines per page
        r = lambda v: max(1, int(round(v * u)))   # noqa: E731
        items.append(Item(PAGE_W / 2, r(2), rng.choice(CLASSIFICATIONS), "cour_b", size, "center"))
        items.append(Item(PAGE_W / 2, r(4), rng.choice(OFFICES), "cour_b", size, "center"))
        items.append(Item(PAGE_W / 2, r(4) + 1, "WASHINGTON, D.C.", "cour", size, "center"))
        items.append(Item(MARGIN_L, r(7), _date(rng, upper=True), "cour", size))
        items.append(Item(MARGIN_L, r(9), "MEMORANDUM FOR: " + rng.choice(ADDRESSEES), "cour", size))
        items.append(Item(MARGIN_L, r(9) + 1, "SUBJECT: " + rng.choice(SUBJECTS), "cour", size))
        _body(rng, items, MEMO_SENTENCES, "cour", size, r(9) + 3, rows - 5, L, right=right["memo"], numbered=True,
              text_amount=text_amount, blank_p=0.45)
        items.append(Item(MARGIN_R - 0.08 * PAGE_W, rows - 3, rng.choice(SIGNERS), "cour", size, "right"))
        items.append(Item(PAGE_W / 2, rows - 1, rng.choice(FOOTERS), "cour", size, "center", ink=0.75))
        ink = (52, 52, 54)
    else:  # sealed / privacy: almost blank sheet, a note at the top and a name label over the portrait
        size = L * FONT_SCALE["arial"]
        note = "*Sealed by order of the Court" if style == "sealed" else "*Redactions for personal privacy information"
        items.append(Item(MARGIN_L + 0.01 * PAGE_W, max(1, int(round(26 / L))), note, "arial", size * 0.95))
        name_row = max(3, int((content_top - phase) // L) - 1)
        items.append(Item(MARGIN_L + rng.uniform(0.22, 0.42) * PAGE_W, name_row,
                          f"{rng.choice(SURNAMES)}, {rng.choice(FIRSTNAMES)}", "arial", size))
        if rng.random() < 0.5:
            items.append(Item(PAGE_W / 2, rows - 1, rng.choice(FOOTERS).title(), "arial", size * 0.9, "center",
                              ink=0.6))
        ink = (40, 40, 42)
    if rng.random() < stamp_p:
        stamp = {
            "text": rng.choice(STAMPS),
            "cx": rng.uniform(0.55, 0.8) * PAGE_W,
            "cy": rng.uniform(0.035, 0.12) * PAGE_H,
            "angle": rng.uniform(-14, 9),
            "size": L * rng.uniform(1.15, 1.6),
            "alpha": rng.uniform(0.55, 0.85),
        }
    return Layout(style, L, phase, items, stamp, ink, name_row)


# --------------------------------------------------------------------------------------------- raster
def row_top(layout, row):
    return layout.phase + row * layout.L


def render_ink(layout, k, pw, ph):
    """Rasterise the text of a layout at k px per design unit; returns uint8 ink coverage (ph, pw)."""
    img = Image.new("L", (pw, ph), 0)
    d = ImageDraw.Draw(img)
    for it in layout.items:
        f = font(it.font, it.size * k)
        w = f.getlength(it.text)
        x = it.x * k
        if it.align == "center":
            x -= w / 2
        elif it.align == "right":
            x -= w
        top = row_top(layout, it.row) * k
        # Centre the glyph box (ascender..baseline) in the row.
        asc, desc = f.getmetrics()
        y = top + (layout.L * k - (asc + desc)) / 2 + desc * 0.25
        d.text((x, y), it.text, fill=int(255 * it.ink), font=f)
    return np.asarray(img)


def render_stamp(stamp, k):
    """The stamp as an RGBA-less (L) ink image, rotated; returns (ink uint8 array, x0, y0) in page px."""
    if not stamp:
        return None
    size = stamp["size"] * k
    f = font("arial_b", size)
    tw = f.getlength(stamp["text"])
    asc, desc = f.getmetrics()
    pad = size * 0.35
    bw = max(1, int(round(size * 0.12)))
    w, h = int(tw + pad * 2 + bw * 2), int(asc + desc + pad * 1.2 + bw * 2)
    img = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(img)
    d.rectangle((bw // 2, bw // 2, w - 1 - bw // 2, h - 1 - bw // 2), outline=255, width=bw)
    d.text((pad + bw, (h - asc - desc) / 2), stamp["text"], fill=255, font=f)
    img = img.rotate(stamp["angle"], resample=Image.BICUBIC, expand=True)
    arr = np.asarray(img)
    x0 = int(stamp["cx"] * k - arr.shape[1] / 2)
    y0 = int(stamp["cy"] * k - arr.shape[0] / 2)
    return arr, x0, y0
