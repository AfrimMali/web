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
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
SITE = ROOT / "site.json"
ITEMS = ROOT / "content" / "items.json"
TEMPLATE = ROOT / "templates" / "base.html"
STATIC = ROOT / "static"
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


def score_of(item):
    """An item's score as a number, whatever the harvest actually emitted.

    A harvest emitting score as "85" for one item and 85 for another would raise
    TypeError in sorted() and kill the whole build. Shared with publish.py so the
    review page and the site can never disagree about what an item scored.
    """
    try:
        return float(item.get("score") or 0)
    except (TypeError, ValueError):
        return 0.0


def sort_key(item):
    return (str(item.get("published") or ""), score_of(item))


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


def copy_static():
    """Copy static/ verbatim into dist/.

    The icons are binary and hand-made, so they are committed source rather than
    build output - dist/ is gitignored and rebuilt from scratch by CI on every
    push, which would otherwise delete them. copy2 keeps mtimes so an unchanged
    icon does not churn the Pages artifact.
    """
    if not STATIC.is_dir():
        return
    for src in sorted(STATIC.iterdir()):
        if src.is_file():
            shutil.copy2(src, DIST / src.name)


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
    where = site['url'].rstrip('/') + f"/items/{slug_for(item)}.html"
    return build_page(
        site, tpl,
        page_title=f"{item['title']} — {site['title']}",
        desc=str(item.get("why") or item["title"]),
        path=f"items/{slug_for(item)}.html",
        main=main,
        items=[item],
        ld=article_ld(site, item, where),
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


def article_ld(site, item, url):
    """Structured data for a single finding.

    The listing pages are collections; one finding is an Article. Emitting
    CollectionPage here told crawlers that the site's own writing was a list of
    links, on precisely the pages that exist to be found.
    """
    return escape_ld(json.dumps({
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": str(item["title"])[:110],
        "description": str(item.get("why") or item["title"]),
        "url": url,
        "inLanguage": site.get("lang", "en"),
        "datePublished": (parse_date(item.get("published")) or datetime.now(timezone.utc))
                         .strftime("%Y-%m-%d"),
        "isBasedOn": item["url"],
        "citation": str(item.get("source", "")),
        "publisher": {"@type": "Organization", "name": site["title"]},
    }, ensure_ascii=False))


def escape_ld(blob):
    """json.dumps does not escape "/", so a title containing </script> would close
    the ld+json block and everything after it would parse as live markup. These are
    valid JSON escapes, so the payload still parses."""
    return (blob.replace("<", "\\u003c")
                .replace(">", "\\u003e")
                .replace("&", "\\u0026"))


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

    return escape_ld(blob)


def csp(tpl, site, extra=None):
    """Content-Security-Policy for the meta tag.

    GitHub Pages cannot send response headers, so this goes in <meta>. Hashes
    are computed from the template's own inline scripts at build time, so the
    policy can never drift out of sync with them. frame-ancestors is omitted
    because meta CSP does not support it.

    `extra` widens named directives for ONE page. The subscribe page needs
    Turnstile and the Google Forms endpoint; no other page does, and a policy
    loosened everywhere to suit one page is not much of a policy.
    """
    def digests(tag):
        return " ".join(
            "'sha256-" + base64.b64encode(hashlib.sha256(s.encode()).digest()).decode() + "'"
            for s in re.findall(f"<{tag}>(.*?)</{tag}>", tpl, re.S))

    script, connect = f"'self' {digests('script')}", "'none'"
    if site.get("fathom_site_id"):
        script += " https://cdn.usefathom.com"
        connect = "https://cdn.usefathom.com"

    policy = {
        "default-src": "'none'",
        "script-src": script,
        # The <style> block is static, so it hashes like the scripts do and
        # 'unsafe-inline' is unnecessary. Injected CSS can exfiltrate data via
        # attribute selectors and background-image URLs, so this is worth closing.
        # The googleapis origin covers the linked font stylesheet.
        "style-src": f"{digests('style')} https://fonts.googleapis.com",
        "font-src": "https://fonts.gstatic.com",
        "img-src": "'self' data:",
        "connect-src": connect,
        "base-uri": "'none'",
        "form-action": "'none'",
    }

    for name, origins in (extra or {}).items():
        # "'none' https://x" is not a widened directive - it is an invalid one, and
        # browsers drop it whole. A directive at 'none' is replaced, not appended to.
        current = policy.get(name, "")
        policy[name] = origins if current in ("'none'", "") else f"{current} {origins}"

    return "; ".join(f"{k} {v}".strip() for k, v in policy.items())


# The origins the subscribe page needs, and the only page that ever gets them.
# script-src and frame-src are what Cloudflare documents for Turnstile; it needs no
# style-src exception, so "no unsafe-inline anywhere" survives. form-action covers
# the no-javascript fallback POST, connect-src the fetch that replaces it.
SUBSCRIBE_CSP = {
    "script-src": "https://challenges.cloudflare.com",
    "frame-src": "https://challenges.cloudflare.com",
    "form-action": "https://docs.google.com",
    "connect-src": "https://docs.google.com",
}


def subscribe_config(site):
    """The three values the subscribe page needs, or None if any is missing.

    All three or nothing. A form without its entry id posts into the void, and a
    page carrying no sitekey renders a widget that never resolves - half-configured
    fails silently in ways that look like working, so it is treated as absent.
    """
    raw = site.get("subscribe") or {}
    got = {k: str(raw.get(k) or "").strip()
           for k in ("form_id", "entry_id", "turnstile_sitekey")}
    return got if all(got.values()) else None


def nav_links(site):
    """Header links, with Subscribe appearing only once it would work."""
    links = list(site.get("links", []))
    if subscribe_config(site):
        links.append({"label": "Subscribe", "href": "/subscribe.html"})
    return links


def build_page(site, tpl, *, page_title, desc, path, main, items, lede="", ld=None,
               csp_extra=None, head_extra=""):
    base = site["url"].rstrip("/")
    canonical = f"{base}/{path}".replace("/index.html", "/")

    nav = "".join(
        f'<a href="{esc(l["href"])}">{esc(l["label"])}</a>' for l in nav_links(site)
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
        "{{CSP}}": esc(csp(tpl, site, csp_extra)),
        "{{HEAD_EXTRA}}": head_extra,
        "{{CANONICAL}}": esc(canonical),
        "{{NAV}}": nav,
        "{{LEDE}}": lede,
        "{{MAIN}}": main,
        "{{UPDATED}}": "{0.day} {0:%B %Y}".format(datetime.now(timezone.utc)),
        "{{JSONLD}}": ld if ld is not None else jsonld(site, page_title, canonical, items),
        "{{FATHOM}}": fathom,
    }
    # One pass, so substituted values are never re-scanned. Item text that happens to
    # contain a literal {{TOKEN}} is left alone instead of being expanded on a later
    # pass -- matters once Hermes writes scraped content into items.json.
    return re.sub(r"\{\{[A-Z_]+\}\}",
                  lambda m: values.get(m.group(0), m.group(0)), tpl)


def build_subscribe(site, tpl, cfg):
    """The email signup page.

    One field posting to a Google Form, which is the whole backend: a static site
    on Pages has nowhere to receive a POST. The form keeps a real action and
    method, so with javascript off it still submits - that fallback is the only
    path that can actually confirm receipt, because Google sends no CORS headers
    and the fetch below can never see its own result.
    """
    action = ("https://docs.google.com/forms/d/e/"
              f"{esc(cfg['form_id'])}/formResponse")
    main = f"""<p class="sub-lede">An email when new findings go up. No more than one a day,
    and nothing else — the same items, in the same words, as the ones on this page.</p>

  <form id="subscribe-form" class="subscribe" action="{action}" method="post"
        target="_blank" rel="noopener">
    <label class="vh" for="subscribe-email">Email address</label>
    <input id="subscribe-email" name="{esc(cfg['entry_id'])}" type="email" required
           autocomplete="email" maxlength="256" placeholder="Enter your email">
    <div class="cf-turnstile" data-sitekey="{esc(cfg['turnstile_sitekey'])}"
         data-theme="auto" data-appearance="interaction-only"></div>
    <button type="submit">Subscribe for future updates</button>
  </form>

  <p id="subscribe-note" class="sub-note" role="status" hidden></p>

  <p id="subscribe-done" class="sub-done" tabindex="-1" hidden>Thank you — that address is
    on the list. You will hear from this site only when something is published.</p>

  <p class="sub-privacy"><a href="/privacy.html">Privacy policy</a></p>"""

    return build_page(
        site, tpl,
        page_title=f"Subscribe — {site['title']}",
        desc=f"Get an email when {site['title']} publishes something new.",
        path="subscribe.html",
        main=main,
        items=[],
        csp_extra=SUBSCRIBE_CSP,
        head_extra='<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"'
                   ' async defer></script>',
    )


def build_privacy(site, tpl):
    """What is collected and why.

    Written plainly and kept true to what the site actually does: while there is no
    analytics id configured, this says so outright rather than hedging. If that ever
    changes, this page is the first thing that has to change with it.
    """
    contact = esc(site.get("security_contact") or "")
    reach = (f'<a href="mailto:{contact}">{contact}</a>' if contact
             else "the address in the site footer")
    analytics = ("" if site.get("fathom_site_id") else
                 """<p>There is no analytics on this site, no cookies, and no third-party
    tracking of any kind. Nothing is stored in your browser except the light or dark
    setting you choose, which never leaves it.</p>

  """)
    return build_page(
        site, tpl,
        page_title=f"Privacy — {site['title']}",
        desc=f"What {site['title']} collects, why, and how to have it removed.",
        path="privacy.html",
        main=f"""{analytics}<h2>If you subscribe</h2>

  <p><strong>What is collected.</strong> Your email address, and nothing else. No name, no
    account, no record of what you open.</p>

  <p><strong>Why.</strong> Solely to send you the findings when they are published. It is
    used for nothing else and never will be.</p>

  <p><strong>The legal basis is your consent.</strong> You gave it by entering the address,
    and you can withdraw it at any time by unsubscribing.</p>

  <p><strong>Where it is held.</strong> In a Google Form and the sheet behind it, readable
    only by the author of this site. Google processes it as part of providing that service.</p>

  <p><strong>Sharing.</strong> Your address is never sold, rented, or given to anyone.</p>

  <p><strong>How long.</strong> Until you unsubscribe, at which point it is deleted.</p>

  <p><strong>Unsubscribing.</strong> Every email carries an unsubscribe address. You can
    also write to {reach} and ask to be removed — no reason needed, and it will be done.</p>

  <p><strong>Your rights.</strong> You can ask what is held about you, ask for it to be
    corrected, or ask for it to be erased. Write to {reach}.</p>""",
        items=[],
    )


def unsubscribe_to(site):
    """The address a reader writes to in order to be removed.

    Falls back to the security contact rather than to nothing: a mail with no way
    off the list is the one thing a newsletter must never be, and an address that
    at least reaches a human beats a dead link.
    """
    sub = site.get("subscribe") or {}
    return (str(sub.get("unsubscribe") or "").strip()
            or str(site.get("security_contact") or "").strip())


def build_email(site, items, day):
    """The day's items as an email: (html, plain text).

    Not built from the page template. Mail clients strip <style> blocks and know
    nothing of custom properties, so every rule here is inline and the palette is
    literal. Items link back to their page on the site rather than to the source,
    so the brief travels with the link.
    """
    base = site["url"].rstrip("/")
    off = unsubscribe_to(site)
    bye_html = (f'<a href="mailto:{esc(off)}?subject=Unsubscribe" '
                f'style="color:#5E5D59">Unsubscribe</a>' if off else "Unsubscribe")
    bye_text = f"Unsubscribe: email {off} with the subject Unsubscribe" if off else ""

    blocks, lines = [], []
    for i in items:
        link = f"{base}/items/{slug_for(i)}.html"
        label = site["pillars"].get(i["pillar"], i["pillar"])
        blocks.append(
            f'<tr><td style="padding:0 0 26px">'
            f'<div style="font:12px/1.4 Helvetica,Arial,sans-serif;color:#5E5D59;'
            f'text-transform:uppercase;letter-spacing:.08em">'
            f'{esc(label)} &middot; {esc(i["source"])}</div>'
            f'<div style="margin:6px 0 4px"><a href="{esc(link)}" '
            f'style="font:700 18px/1.35 Georgia,serif;color:#1F1E1D;text-decoration:none">'
            f'{esc(i["title"])}</a></div>'
            f'<div style="font:16px/1.5 Georgia,serif;color:#3D3D3A">'
            f'{esc(i.get("why") or "")}</div></td></tr>')
        lines.append(f'{label} / {i["source"]}\n{i["title"]}\n'
                     f'{i.get("why") or ""}\n{link}\n')

    plural = "" if len(items) == 1 else "s"
    html = (
        '<!doctype html><html><body style="margin:0;padding:0;background:#F0EEE6">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#F0EEE6"><tr><td align="center" style="padding:32px 16px">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="max-width:560px">'
        f'<tr><td style="padding:0 0 8px"><a href="{esc(base)}/" '
        'style="font:700 26px/1.1 Georgia,serif;color:#1F1E1D;text-decoration:none">'
        f'{esc(site["title"])}</a></td></tr>'
        '<tr><td style="font:14px/1.5 Georgia,serif;color:#5E5D59;padding:0 0 28px">'
        f'{esc(day)} &middot; {len(items)} finding{plural}</td></tr>'
        + "".join(blocks) +
        '<tr><td style="border-top:1px solid #D1CFC5;padding:18px 0 0;'
        'font:12px/1.6 Helvetica,Arial,sans-serif;color:#5E5D59">'
        'You are getting this because you asked for an email when '
        f'{esc(site["title"])} publishes. {bye_html}</td></tr>'
        '</table></td></tr></table></body></html>\n')

    text = (f'{site["title"]} - {day} - {len(items)} finding{plural}\n\n'
            + "\n".join(lines)
            + f'\n---\n{bye_text}\n')
    return html, text


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
    # One date for every url tells a crawler nothing and teaches it to ignore
    # lastmod - the same failure the comment above warns about, by another route.
    # Listing pages move when the newest item does; an item page moves when it
    # was published.
    entries = [("/", day), ("/archive.html", day), ("/privacy.html", day)]
    if subscribe_config(site):
        entries.append(("/subscribe.html", day))
    for i in items:
        d = parse_date(i.get("published")) or newest
        entries.append((f"/items/{slug_for(i)}.html",
                        (d or datetime.now(timezone.utc)).strftime("%Y-%m-%d")))
    urls = "".join(
        f"\n  <url><loc>{base}{p}</loc><lastmod>{when}</lastmod></url>"
        for p, when in entries
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

    write(DIST / "privacy.html", build_privacy(site, tpl))

    cfg = subscribe_config(site)
    if cfg:
        write(DIST / "subscribe.html", build_subscribe(site, tpl, cfg))

    pages = DIST / "items"
    pages.mkdir(exist_ok=True)
    for item in items:
        write(pages / f"{slug_for(item)}.html", build_item(site, tpl, item))

    write(DIST / "feed.xml", atom(site, items))
    write(DIST / "sitemap.xml", sitemap(site, items, payload))
    write(DIST / "robots.txt",
          f"User-agent: *\nAllow: /\nSitemap: {site['url'].rstrip('/')}/sitemap.xml\n")
    write(DIST / ".nojekyll", "")
    copy_static()

    sec = security_txt(site)
    if sec:
        (DIST / ".well-known").mkdir(exist_ok=True)
        write(DIST / ".well-known" / "security.txt", sec)

    print(f"wrote {DIST}/ — index, archive, privacy, feed, sitemap, robots, icons"
          + (", subscribe" if cfg else "")
          + ("" if cfg else "   (no subscribe page: site.json 'subscribe' is not filled in)"))

    if args.serve:
        import http.server, socketserver, os
        os.chdir(DIST)
        print("http://localhost:8000  (ctrl-c to stop)")
        # Threading, for the same reason publish.py's review server needs it: an
        # HTTP/1.1 keep-alive connection from a browser is held open, and a
        # single-connection loop then blocks every other request behind it.
        socketserver.ThreadingTCPServer.allow_reuse_address = True
        socketserver.ThreadingTCPServer(("127.0.0.1", 8000),
                                        http.server.SimpleHTTPRequestHandler).serve_forever()


if __name__ == "__main__":
    main()
