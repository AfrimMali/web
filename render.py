#!/usr/bin/env python3
"""
render.py — turns content/items.json into a static site.

Standard library only. No npm, no bundler, no framework, no build step beyond
this file. Output is plain HTML that a crawler indexes on first pass.

    python render.py            build into dist/
    python render.py --serve    build, then serve dist/ on :8000
    python render.py --validate check items.json without building
"""

import argparse
import html
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
SITE = ROOT / "site.json"
ITEMS = ROOT / "content" / "items.json"
TEMPLATE = ROOT / "templates" / "base.html"
DIST = ROOT / "dist"

REQUIRED = ("title", "url", "source", "pillar")


# ---------- load & validate ----------

def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"missing {path.relative_to(ROOT)}")
    except json.JSONDecodeError as e:
        sys.exit(f"{path.relative_to(ROOT)} is not valid JSON: {e}")


def validate(payload, pillars):
    """Drop malformed items loudly rather than emitting a broken page."""
    raw = payload.get("items", [])
    if not isinstance(raw, list):
        sys.exit("items.json: 'items' must be a list")

    good, seen = [], set()
    for n, item in enumerate(raw):
        if not isinstance(item, dict):
            print(f"  skip #{n}: not an object", file=sys.stderr)
            continue
        missing = [f for f in REQUIRED if not item.get(f)]
        if missing:
            print(f"  skip #{n}: missing {', '.join(missing)}", file=sys.stderr)
            continue
        if not str(item["url"]).startswith(("http://", "https://")):
            print(f"  skip #{n}: url is not absolute", file=sys.stderr)
            continue
        if item["pillar"] not in pillars:
            print(f"  skip #{n}: unknown pillar {item['pillar']!r}", file=sys.stderr)
            continue
        if item["url"] in seen:
            print(f"  skip #{n}: duplicate url {item['url']}", file=sys.stderr)
            continue
        seen.add(item["url"])
        good.append(item)
    return good


def sort_key(item):
    # Coerce both fields: a scraper emitting score as "85" for one item and 85 for
    # another would otherwise raise TypeError in sorted() and kill the whole build.
    try:
        score = float(item.get("score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    return (str(item.get("published") or ""), score)


# ---------- helpers ----------

# Control characters that are illegal in XML at any escaping level. html.escape
# leaves them alone, and one of them in a scraped title makes feed.xml unparseable.
_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def esc(s):
    return html.escape(_ILLEGAL_XML.sub("", str(s or "")), quote=True)


def parse_date(value):
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            d = datetime.strptime(str(value), fmt)
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def short_date(value):
    # %-d is glibc-only; build the day number directly so this runs on Windows too.
    d = parse_date(value)
    return f"{d.day} {d:%b}" if d else ""


def rfc3339(value):
    d = parse_date(value)
    return (d or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------- markup ----------

def render_item(item):
    bits = [esc(item["source"])]
    if short_date(item.get("published")):
        bits.append(short_date(item["published"]))
    meta = ", ".join(bits)
    why = (f'\n        <p class="why">{esc(item["why"])}</p>'
           if item.get("why") else "")
    return f"""      <li class="item">
        <a class="t" href="{esc(item['url'])}" rel="noopener">{esc(item['title'])}</a>
        <span class="meta">({meta})</span>{why}
      </li>"""


def render_sections(items, pillars):
    out = []
    for key, label in pillars.items():
        group = [i for i in items if i["pillar"] == key]
        if not group:
            continue
        rows = "\n".join(render_item(i) for i in group)
        out.append(f'    <section>\n      <h2>{esc(label)}</h2>\n'
                   f'      <ul>\n{rows}\n      </ul>\n    </section>')
    return "\n".join(out)


def render_archive(items):
    buckets = {}
    for item in items:
        d = parse_date(item.get("published"))
        buckets.setdefault(d.strftime("%B %Y") if d else "Undated", []).append(item)

    out = []
    for label, group in buckets.items():
        rows = "\n".join(render_item(i) for i in group)
        out.append(f'    <h2 class="month">{esc(label)}</h2>\n'
                   f'    <ul>\n{rows}\n    </ul>')
    return "\n".join(out)


def jsonld(site, page_title, url, items):
    blob = json.dumps({
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": page_title,
        "description": site["tagline"],
        "url": url,
        "inLanguage": site.get("lang", "en"),
        "mainEntity": {
            "@type": "ItemList",
            "numberOfItems": len(items),
            "itemListElement": [
                {"@type": "ListItem", "position": n + 1,
                 "url": i["url"], "name": i["title"]}
                for n, i in enumerate(items[:30])
            ],
        },
    }, ensure_ascii=False)

    # json.dumps does not escape "/", so a title containing </script> would close
    # the ld+json block and everything after it would parse as live markup. These
    # are valid JSON escapes, so the payload still parses.
    return (blob.replace("<", "\\u003c")
                .replace(">", "\\u003e")
                .replace("&", "\\u0026"))


def build_page(site, tpl, *, page_title, desc, path, main, items, lede=""):
    base = site["url"].rstrip("/")
    canonical = f"{base}/{path}".replace("/index.html", "/")

    nav = "".join(
        f'<a href="{esc(l["href"])}">{esc(l["label"])}</a>' for l in site.get("links", [])
    )

    fathom = ""
    if site.get("fathom_site_id"):
        fathom = (f'<script src="https://cdn.usefathom.com/script.js" '
                  f'data-site="{esc(site["fathom_site_id"])}" '
                  f'data-honor-dnt="true" defer></script>')

    values = {
        "{{LANG}}": esc(site.get("lang", "en")),
        "{{SITE_TITLE}}": esc(site["title"]),
        "{{PAGE_TITLE}}": esc(page_title),
        "{{DESC}}": esc(desc),
        "{{AUTHOR}}": esc(site.get("author", "")),
        "{{CANONICAL}}": esc(canonical),
        "{{NAV}}": nav,
        "{{LEDE}}": lede,
        "{{MAIN}}": main,
        "{{UPDATED}}": "{0.day} {0:%B %Y}".format(datetime.now(timezone.utc)),
        "{{JSONLD}}": jsonld(site, page_title, canonical, items),
        "{{FATHOM}}": fathom,
    }
    # One pass, so substituted values are never re-scanned. Item text that happens to
    # contain a literal {{TOKEN}} is left alone instead of being expanded on a later
    # pass -- matters once Hermes writes scraped content into items.json.
    return re.sub(r"\{\{[A-Z_]+\}\}",
                  lambda m: values.get(m.group(0), m.group(0)), tpl)


# ---------- feeds ----------

def atom(site, items):
    base = site["url"].rstrip("/")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entries = "".join(f"""
  <entry>
    <title>{esc(i['title'])}</title>
    <link href="{esc(i['url'])}"/>
    <id>{esc(i['url'])}</id>
    <updated>{rfc3339(i.get('published'))}</updated>
    <author><name>{esc(i['source'])}</name></author>
    <summary>{esc(i.get('why', ''))}</summary>
  </entry>""" for i in items[:50])

    return f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>{esc(site['title'])}</title>
  <subtitle>{esc(site['tagline'])}</subtitle>
  <link href="{base}/feed.xml" rel="self"/>
  <link href="{base}/"/>
  <id>{base}/</id>
  <updated>{now}</updated>{entries}
</feed>
"""


def sitemap(site, items, payload=None):
    base = site["url"].rstrip("/")
    # Derive lastmod from the content, not the clock. The daily cron rebuilds even
    # when nothing changed; restamping today's date each time teaches crawlers to
    # ignore lastmod entirely.
    dates = [d for d in (parse_date(i.get("published")) for i in items) if d]
    newest = max(dates, default=None) or parse_date((payload or {}).get("generated_at"))
    day = (newest or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    urls = "".join(
        f"\n  <url><loc>{base}{p}</loc><lastmod>{day}</lastmod></url>"
        for p in ("/", "/archive.html")
    )
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f'{urls}\n</urlset>\n')


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--validate", action="store_true")
    args = ap.parse_args()

    site = load_json(SITE)
    pillars = site["pillars"]
    payload = load_json(ITEMS)

    items = sorted(validate(payload, pillars), key=sort_key, reverse=True)
    print(f"{len(items)} valid item(s)")
    if args.validate:
        return
    if not items:
        print("warning: nothing to publish", file=sys.stderr)

    tpl = TEMPLATE.read_text(encoding="utf-8")
    recent = items[: site.get("index_items", 20)]

    lede = f"""<p class="lede">{esc(site['tagline'])}
    <button class="more" id="more" type="button" aria-expanded="false"
            aria-controls="about">More</button></p>
  <div class="about" id="about" hidden>{esc(site['about'])}</div>"""

    DIST.mkdir(exist_ok=True)

    (DIST / "index.html").write_text(build_page(
        site, tpl,
        page_title=site["title"],
        desc=site["tagline"],
        path="index.html",
        main=render_sections(recent, pillars) or '<p class="empty">Nothing cleared the bar today.</p>',
        items=recent,
        lede=lede,
    ), encoding="utf-8")

    (DIST / "archive.html").write_text(build_page(
        site, tpl,
        page_title=f"Archive — {site['title']}",
        desc=f"Everything published on {site['title']}, newest first.",
        path="archive.html",
        main=render_archive(items),
        items=items,
    ), encoding="utf-8")

    (DIST / "feed.xml").write_text(atom(site, items), encoding="utf-8")
    (DIST / "sitemap.xml").write_text(sitemap(site, items, payload), encoding="utf-8")
    (DIST / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {site['url'].rstrip('/')}/sitemap.xml\n",
        encoding="utf-8")
    (DIST / ".nojekyll").write_text("", encoding="utf-8")

    print(f"wrote {DIST}/ — index, archive, feed, sitemap, robots")

    if args.serve:
        import http.server, socketserver, os
        os.chdir(DIST)
        print("http://localhost:8000  (ctrl-c to stop)")
        socketserver.TCPServer(("", 8000),
                               http.server.SimpleHTTPRequestHandler).serve_forever()


if __name__ == "__main__":
    main()
