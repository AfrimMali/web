#!/usr/bin/env python3
"""make_cards.py — the social share card for each item, drawn once and committed.

A link with no og:image renders as bare text in Slack, WhatsApp, X and iMessage.
This draws one 1200x630 card per item, carrying the headline, so a shared link
looks like something rather than nothing.

Deliberately NOT part of render.py or the build. The workflow says "No pip
install. render.py is standard library only", and drawing text needs Pillow, so
this runs here and the results are committed. Cards are named from the same
slug_for() the page URL uses, so the image can never point at a page that does
not exist.

    python tools/make_cards.py            draw whatever is missing
    python tools/make_cards.py --force    redraw everything

Re-running is cheap: an item whose card already exists is skipped.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import render                                                    # noqa: E402

FONT = Path(__file__).parent / "Newsreader.ttf"
OUT = ROOT / "static" / "og"

W, H = 1200, 630
MARGIN = 84
INK = (31, 30, 29)          # --fg
GROUND = (240, 238, 230)    # --bg
MUTED = (94, 93, 89)        # --muted
RULE = (209, 207, 197)      # --rule


def face(size, weight=400, optical=40):
    """Newsreader at a weight and optical size.

    The axes come back in the font's own order - Weight, then Optical Size - and
    a build of Pillow without variable-font support raises rather than silently
    ignoring it, so fall back to the default instance instead of dying.
    """
    from PIL import ImageFont
    f = ImageFont.truetype(str(FONT), size)
    try:
        f.set_variation_by_axes([weight, optical])
    except (OSError, AttributeError):
        pass
    return f


def wrap(draw, text, font, width):
    """Greedy wrap by measured width, because a character count lies badly on a
    proportional face - 'Illinois' and 'MMMMMMMM' are not the same width."""
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


LEAD = 1.2                       # line height, as a multiple of the size


def fit(draw, text, width, height, sizes):
    """Largest size from `sizes` whose wrapped block fits the box.

    Both dimensions, not just the line count: measuring width alone let a long
    headline set four big lines straight through the footer rule. Sizes are tried
    biggest first, because a card is read at thumbnail size or not at all.
    """
    for size in sizes:
        font = face(size, weight=600, optical=40)
        lines = wrap(draw, text, font, width)
        if len(lines) * size * LEAD <= height:
            return font, lines

    # Nothing fits: set it at the smallest size, keep what the box holds, and
    # mark the cut so the card never reads as a complete sentence that is not one.
    size = sizes[-1]
    font = face(size, weight=600, optical=40)
    keep = max(1, int(height // (size * LEAD)))
    lines = wrap(draw, text, font, width)[:keep]
    lines[-1] = lines[-1].rstrip(" .,;:") + "…"
    return font, lines


def tracked(draw, xy, text, font, fill, extra=2.0):
    """Draw with letter-spacing, which Pillow has no setting for."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + extra


def card(title, kicker, footer):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (W, H), GROUND)
    d = ImageDraw.Draw(im)
    inner = W - MARGIN * 2

    # The vertical budget, stated once: everything below has to live inside it.
    rule_y = H - MARGIN - 52
    top = MARGIN + (132 if kicker else 96)
    box = rule_y - 30 - top

    tracked(d, (MARGIN, MARGIN - 10), "Signal", face(46, 700, 60), INK, 0.5)

    if kicker:
        tracked(d, (MARGIN, MARGIN + 62), kicker.upper(), face(24, 500, 20), MUTED, 3.0)

    font, lines = fit(d, title, inner, box, (76, 68, 60, 52, 46, 40, 34))
    # Sit the block on the bottom of its box, so short and long headlines share a
    # baseline instead of each floating at a different height.
    y = rule_y - 30 - len(lines) * font.size * LEAD
    for line in lines:
        d.text((MARGIN, y), line, font=font, fill=INK)
        y += font.size * LEAD

    d.line([(MARGIN, rule_y), (W - MARGIN, rule_y)], fill=RULE, width=2)
    d.text((MARGIN, rule_y + 18), footer, font=face(26, 400, 20), fill=MUTED)
    return im


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="redraw cards that already exist")
    args = ap.parse_args()

    try:
        import PIL                                               # noqa: F401
    except ImportError:
        sys.exit("Pillow is needed to draw the cards:  pip install --user Pillow")

    site = render.load_json(render.SITE)
    items = render.validate(render.load_json(render.ITEMS), site["pillars"])
    OUT.mkdir(parents=True, exist_ok=True)

    made = skipped = 0
    wanted = {"default.png"}

    path = OUT / "default.png"
    wanted.add(path.name)
    if args.force or not path.exists():
        card(site["tagline"], "", site["url"].replace("https://", "")).save(path, optimize=True)
        made += 1
    else:
        skipped += 1

    for item in items:
        path = OUT / f"{render.slug_for(item)}.png"
        wanted.add(path.name)
        if path.exists() and not args.force:
            skipped += 1
            continue
        card(item["title"],
             site["pillars"].get(item["pillar"], item["pillar"]),
             item["source"]).save(path, optimize=True)
        made += 1

    # An item can never lose its page - slug_for() is derived from title and
    # source, and neither is editable once published - so a stray card means a
    # rename slipped through somewhere. Say so rather than quietly leaving it.
    stray = sorted(p.name for p in OUT.glob("*.png") if p.name not in wanted)
    print(f"{made} drawn, {skipped} already there  ->  {OUT}")
    if stray:
        print(f"  {len(stray)} card(s) match no published item: {', '.join(stray[:5])}")


if __name__ == "__main__":
    main()
