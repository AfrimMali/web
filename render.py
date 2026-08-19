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
import base64
import hashlib
import html
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
SITE = ROOT / "site.json"
ITEMS = ROOT / "content" / "items.json"
TEMPLATE = ROOT / "templates" / "base.html"
DIST = ROOT / "dist"

REQUIRED = ("title", "url", "source", "pillar")

# Characters that must never survive into the output.
#   - C0/C1 controls are illegal in XML and break the feed.
#   - Lone surrogates get through json.loads(\uD800) and then raise
#     UnicodeEncodeError at write time, killing the whole build.
#   - The bidi overrides and isolates are the Trojan Source set: they let a
#     scraped headline render as text it does not contain.
# ZWNJ/ZWJ (200c/200d) and LRM/RLM (200e/200f) are deliberately NOT stripped -
# they are load-bearing in Arabic, Persian, Indic scripts and emoji sequences.
_STRIP = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f"      # control characters
    "\ud800-\udfff"                              # lone surrogates
    "\u200b"                                     # zero-width space
    "\u202a-\u202e\u2066-\u2069"                 # bidi overrides / isolates
    "]")

# A runaway scrape should degrade the page, not replace it.
LIMITS = {"title": 300, "source": 120, "why": 500, "brief": 2500}


def clean(value, limit):
    return _STRIP.sub("", str(value or "")).strip()[:limit]


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

        # Sanitise here rather than in esc(), because jsonld() bypasses esc()
        # entirely. Cleaning at the boundary means every downstream path -
        # HTML, feed, JSON-LD - inherits clean values.
        item = dict(item)
        for field, limit in LIMITS.items():
            if item.get(field) is not None:
                item[field] = clean(item[field], limit)

        # A URL is not a display string: silently rewriting it would point the
        # link somewhere the source never said. Reject instead.
        if item.get("url") is not None and _STRIP.search(str(item["url"])):
            print(f"  skip #{n}: url contains control or bidi characters",
                  file=sys.stderr)
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


def write(path, text):
    """Write LF, always.

    The CSP hashes are computed from the template held in memory, where newlines
    are LF. write_text() defaults to newline=None, which on Windows translates
    them to CRLF on the way to disk - so the bytes the browser hashes are not the
    bytes that were hashed here, and it blocks the style block and every inline
    script. CI builds on Linux and never saw it; a local --serve did.
    """
    path.write_text(text, encoding="utf-8", newline="\n")


# ---------- markup ----------

def render_item(item):
    bits = [esc(item["source"])]
    when = short_date(item.get("published"))
    if when:
        bits.append(when)
    meta = ", ".join(bits)
    why = (f'\n        <p class="why">{esc(item["why"])}</p>'
           if item.get("why") else "")
    return f"""      <li class="item">
        <a class="t" href="/items/{slug_for(item)}.html">{esc(item['title'])}</a>
        <span class="meta">({meta})</span>{why}
      </li>"""


def slug_for(item):
    """Stable, readable path for an item's own page.

    The title makes it readable; a short hash of the url makes it unique and keeps it
    stable when two findings share a headline. Changing this function moves every page
    on the site, so treat its output as a permanent contract rather than a detail.
    """
    base = re.sub(r"[^a-z0-9]+", "-", str(item["title"]).lower()).strip("-")[:60].strip("-")
    tag = hashlib.sha256(str(item["url"]).encode()).hexdigest()[:6]
    return f"{base or 'item'}-{tag}"


def render_brief(item):
    """The item's own prose, as paragraphs.

    This is Hermes' writing, never the source's - reproducing a journal's text would be
    infringement, and the harvest instruction forbids it. Falls back to the one-line
    takeaway for items published before briefs existed.
    """
    text = str(item.get("brief") or item.get("why") or "")
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return "\n".join(f"      <p>{esc(p)}</p>" for p in paras)


def build_item(site, tpl, item):
    """One finding on its own page, so a click keeps the reader here."""
    label = site["pillars"].get(item["pillar"], item["pillar"])
    when = short_date(item.get("published"))
    meta = " · ".join(x for x in (esc(label), esc(when)) if x)
    main = f"""    <article class="entry">
      <p class="stamp">{meta}</p>
      <h1>{esc(item['title'])}</h1>
{render_brief(item)}
      <p class="origin"><a href="{esc(item['url'])}" rel="noopener">Read the original at {esc(item['source'])} &#8594;</a></p>
    </article>"""
    return build_page(
        site, tpl,
        page_title=f"{item['title']} — {site['title']}",
        desc=str(item.get("why") or item["title"]),
        path=f"items/{slug_for(item)}.html",
        main=main,
        items=[item],
    )


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
        # %-d is glibc-only, so build the day number by hand as short_date() does
        label = f"{d.day} {d:%B %Y}" if d else "Undated"
        buckets.setdefault(label, []).append(item)

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


def csp(tpl, site):
    """Content-Security-Policy for the meta tag.

    GitHub Pages cannot send response headers, so this goes in <meta>. Hashes
    are computed from the template's own inline scripts at build time, so the
    policy can never drift out of sync with them. frame-ancestors is omitted
    because meta CSP does not support it.
    """
    def digests(tag):
        return " ".join(
            "'sha256-" + base64.b64encode(hashlib.sha256(s.encode()).digest()).decode() + "'"
            for s in re.findall(f"<{tag}>(.*?)</{tag}>", tpl, re.S))

    script, connect = f"'self' {digests('script')}", "'none'"
    if site.get("fathom_site_id"):
        script += " https://cdn.usefathom.com"
        connect = "https://cdn.usefathom.com"

    return "; ".join((
        "default-src 'none'",
        f"script-src {script}",
        # The <style> block is static, so it hashes like the scripts do and
        # 'unsafe-inline' is unnecessary. Injected CSS can exfiltrate data via
        # attribute selectors and background-image URLs, so this is worth closing.
        # The googleapis origin covers the linked font stylesheet.
        f"style-src {digests('style')} https://fonts.googleapis.com",
        "font-src https://fonts.gstatic.com",
        "img-src 'self' data:",
        f"connect-src {connect}",
        "base-uri 'none'",
        "form-action 'none'",
    ))


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
        "{{CSP}}": esc(csp(tpl, site)),
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


def build_index(site, tpl, items):
    """The index page exactly as it ships, plus the items it actually shows.

    Shared with publish.py so the review preview cannot drift from what the build
    produces. A preview that quietly differs from production is worse than no
    preview at all - it teaches you to trust the wrong thing.
    """
    recent = items[: site.get("index_items", 20)]

    lede = f"""<p class="lede">{esc(site['tagline'])}
    <button class="more" id="more" type="button" aria-expanded="false"
            aria-controls="about">More</button></p>
  <div class="about" id="about" hidden>{esc(site['about'])}</div>"""

    page = build_page(
        site, tpl,
        page_title=site["title"],
        desc=site["tagline"],
        path="index.html",
        main=render_sections(recent, site["pillars"]) or '<p class="empty">Nothing cleared the bar today.</p>',
        items=recent,
        lede=lede,
    )
    return page, recent


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
    <content type="text">{esc(i.get('brief') or i.get('why', ''))}</content>
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


def security_txt(site):
    """RFC 9116 contact file, or None when no address is configured.

    Expires is recomputed on every build, so the daily rebuild keeps it valid
    rather than letting it silently go stale.
    """
    contact = site.get("security_contact", "").strip()
    if not contact:
        return None
    base = site["url"].rstrip("/")
    # timedelta, not replace(year=+1): the latter raises ValueError on 29 February
    # and would fail the daily build outright, once every four years.
    expires = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=365)
    return (f"Contact: mailto:{contact}\n"
            f"Expires: {expires.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"Preferred-Languages: {site.get('lang', 'en')}\n"
            f"Canonical: {base}/.well-known/security.txt\n")


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
        for p in ["/", "/archive.html"] + [f"/items/{slug_for(i)}.html" for i in items]
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
    DIST.mkdir(exist_ok=True)

    index_html, recent = build_index(site, tpl, items)
    write(DIST / "index.html", index_html)

    write(DIST / "archive.html", build_page(
        site, tpl,
        page_title=f"Archive — {site['title']}",
        desc=f"Everything published on {site['title']}, newest first.",
        path="archive.html",
        main=render_archive(items),
        items=items,
    ))

    pages = DIST / "items"
    pages.mkdir(exist_ok=True)
    for item in items:
        write(pages / f"{slug_for(item)}.html", build_item(site, tpl, item))

    write(DIST / "feed.xml", atom(site, items))
    write(DIST / "sitemap.xml", sitemap(site, items, payload))
    write(DIST / "robots.txt",
          f"User-agent: *\nAllow: /\nSitemap: {site['url'].rstrip('/')}/sitemap.xml\n")
    write(DIST / ".nojekyll", "")

    sec = security_txt(site)
    if sec:
        (DIST / ".well-known").mkdir(exist_ok=True)
        write(DIST / ".well-known" / "security.txt", sec)

    print(f"wrote {DIST}/ — index, archive, feed, sitemap, robots")

    if args.serve:
        import http.server, socketserver, os
        os.chdir(DIST)
        print("http://localhost:8000  (ctrl-c to stop)")
        socketserver.TCPServer(("127.0.0.1", 8000),
                               http.server.SimpleHTTPRequestHandler).serve_forever()


if __name__ == "__main__":
    main()
