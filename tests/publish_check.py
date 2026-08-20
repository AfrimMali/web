"""Probes for publish.py — the gate between Hermes' proposal and the live site.

Everything here is pure or in-memory: no git, no network, no server. The guards
that matter for committing are written as pure functions precisely so they can be
tested without a repository. Run from the repo root.
"""
import contextlib, io, json, re, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import render          # noqa: E402
import publish         # noqa: E402

site = render.load_json(render.SITE)
P = site["pillars"]
RLO, ZWSP = "‮", "​"
results = []


def check(name, passed, detail=""):
    results.append((name, passed, detail))


def item(**over):
    base = {"title": "t", "url": "https://example.org/a", "source": "Example",
            "pillar": "health", "published": "2026-08-19", "score": 50}
    base.update(over)
    return base


# --- the JSON has to survive however Hermes decides to wrap it ---------------

GOOD = '{"items": [{"title": "t", "url": "https://e.org/a", "source": "s", "pillar": "health"}]}'
check("extract: bare object", publish.extract_json(GOOD) is not None)
check("extract: fenced in ```json",
      publish.extract_json("Here you go:\n```json\n" + GOOD + "\n```\nHope that helps!") is not None)
check("extract: wrapped in prose",
      publish.extract_json("I found 1 item.\n" + GOOD + "\nThat is all.") is not None)
check("extract: bare list is normalised",
      (publish.extract_json('[{"title":"t","url":"https://e.org/a","source":"s","pillar":"health"}]')
       or {}).get("items") is not None)
check("extract: refuses garbage", publish.extract_json("no json here at all") is None)


# --- the run-output file embeds the prompt, schema example and all ----------
# Hermes' cron runtime records "## Prompt" (the entire skill, which itself contains a
# worked JSON schema AND the characters that open a fence) above "## Response". Read
# the file naively and you can publish the schema example as though it were content.

CRON_FILE = '# Cron Job: signal-harvest\n\n## Prompt\n\nAnswer with a single fenced ```json block. Prose around it is fine.\n\n```json\n{"items": [{"title": "required - the headline", "url": "required - absolute https link",\n            "source": "required", "pillar": "required", "score": 0}]}\n```\n\n## Response\n\nI searched 40 sources; one cleared the bar.\n\n```json\n{"items": [{"title": "The real finding", "url": "https://e.org/real",\n            "source": "s", "pillar": "health", "score": 88}]}\n```\n'

got = publish.extract_json(CRON_FILE)
check("cron file: JSON is found at all", got is not None)
title = ((got or {}).get("items") or [{}])[0].get("title", "")
check("cron file: takes the answer, not the schema example", title == "The real finding", title)
check("cron file: the schema example never leaks through",
      "required" not in json.dumps(got or {}))
check("cron file: prose mentioning a fence mid-sentence is not treated as data",
      publish.response_section(CRON_FILE).count("required") == 0)


# --- a lookalike domain must not be able to hide ----------------------------

ascii_host, non_ascii = publish.host_of("https://сochrane.org/x")   # Cyrillic es
check("host: cyrillic lookalike flagged", non_ascii)
check("host: cyrillic lookalike shown as punycode", ascii_host.startswith("xn--"),
      ascii_host)
check("host: plain domain untouched", publish.host_of("https://cochrane.org/x") == ("cochrane.org", False))
check("host: registrable tail emphasised",
      publish.host_markup("cochrane.org.evil.com").endswith("<b>evil.com</b>"),
      publish.host_markup("cochrane.org.evil.com"))


# --- claimed source vs the domain it actually links to ----------------------

check("source: matching claim is not flagged",
      publish.source_matches_host(item(source="Cochrane", url="https://cochrane.org/a")))
check("source: mismatched claim is flagged",
      not publish.source_matches_host(item(source="Cochrane", url="https://supplement-blog.example/a")))


# --- validate() remains the only authority on what may ship -----------------

hostile = {"items": [
    item(title="Safe" + RLO + "evil" + ZWSP, url="https://e.org/1"),
    item(url="/relative/path"),
    item(pillar="not-a-pillar", url="https://e.org/3"),
    item(url="https://e.org/dupe"),
    item(url="https://e.org/dupe"),
    item(title="", url="https://e.org/5"),
    item(title="A" * 5000, url="https://e.org/6"),
]}
rows, survivors = publish.judge(hostile, site)
kept = [r for r in rows if r["ok"]]

check("judge: relative url dropped",
      any(not r["ok"] and "absolute" in r["reason"] for r in rows))
check("judge: unknown pillar dropped",
      any(not r["ok"] and "pillar" in r["reason"] for r in rows))
check("judge: duplicate url dropped",
      any(not r["ok"] and "duplicate" in r["reason"] for r in rows))
check("judge: missing title dropped",
      any(not r["ok"] and "missing" in r["reason"] for r in rows))
check("judge: bidi and zero-width stripped from what survives",
      all(RLO not in (r["item"] or {}).get("title", "")
          and ZWSP not in (r["item"] or {}).get("title", "") for r in kept))
check("judge: over-long title capped at 300",
      all(len((r["item"] or {}).get("title", "")) <= 300 for r in kept))
check("judge: every rejection carries a reason",
      all(r["reason"] for r in rows if not r["ok"]))
check("judge: survivors match validate()", len(kept) == len(survivors),
      f"{len(kept)} vs {len(survivors)}")


# --- the middle state: shown, unticked, still publishable --------------------
# "How can an approved pill help me in my daily life" - a first-in-class approval is
# valid and worth seeing, but it must not lead the site. It arrives enabled and
# unchecked, which dropped() in admin.html reads as "leave it out unless ticked".

graded = {"items": [
    item(title="Weak but valid", url="https://e.org/weak", score=45),
    item(title="Strong one", url="https://e.org/strong", score=85),
    item(title="Exactly the bar", url="https://e.org/mid", score=publish.PUBLISH_SCORE),
]}
graded_rows, _ = publish.judge(graded, site)
by_title = {r["title"]: r for r in graded_rows}

check("bar: a 45 is usable but not ticked",
      by_title["Weak but valid"]["ok"] and by_title["Weak but valid"]["low"])
check("bar: an 85 is ticked",
      by_title["Strong one"]["ok"] and not by_title["Strong one"]["low"])
check("bar: the bar itself counts as above it", not by_title["Exactly the bar"]["low"])
check("bar: strongest first", graded_rows[0]["title"] == "Strong one",
      graded_rows[0]["title"])
check("bar: reordering does not move `n` off its own item",
      all(graded["items"][r["n"]]["url"] == r["item"]["url"] for r in graded_rows))

blocks = publish.row_markup(graded_rows).split("<li ")
weak = [b for b in blocks if "Weak but valid" in b][0]
strong = [b for b in blocks if "Strong one" in b][0]
check("bar: a weak row is neither ticked nor disabled",
      "checked" not in weak and "disabled" not in weak)
check("bar: and says why it is not ticked", "below the bar" in weak)
check("bar: a strong row is ticked", "checked" in strong)


# --- a link to somewhere this site has never linked before ------------------
# Hermes reads pages an attacker can write. It cannot publish anything itself, so
# the exposure is a proposal carrying a plausible source and a hostile link. This
# does not block - it only says "you have never linked here", which is the one
# thing a human reviewer cannot work out at a glance.

saved_for_hosts = publish.ITEMS
hosts_archive = Path(tempfile.mkdtemp()) / "items.json"
try:
    publish.ITEMS = hosts_archive
    hosts_archive.write_text(json.dumps({"items": [
        item(title="Known", url="https://www.cpsc.gov/Recalls/2026/x"),
    ]}), encoding="utf-8")

    host_rows, _ = publish.judge({"items": [
        item(title="Same domain as before", url="https://www.cpsc.gov/Recalls/2026/y"),
        item(title="Somewhere new", url="https://totally-new.example/a"),
        item(title="Lookalike", url="https://www.cpsc.gov.evil.example/a"),
        item(title="New and strong", url="https://brand-new.example/b", score=90),
    ]}, site)
    seen_by_title = {r["title"]: r for r in host_rows}

    check("host: a domain already in the archive is not flagged",
          not seen_by_title["Same domain as before"]["new_host"])
    check("host: a domain never published before is flagged",
          seen_by_title["Somewhere new"]["new_host"])
    check("host: a lookalike domain counts as new, not as the real one",
          seen_by_title["Lookalike"]["new_host"])
    check("host: the flag never changes what may ship",
          all(r["ok"] for r in host_rows))
    # A warning that quietly unticked things would train you to ignore it.
    strong_new = seen_by_title["New and strong"]
    check("host: a strong item on a new domain is still flagged",
          strong_new["new_host"])
    check("host: and still arrives ticked",
          not strong_new["low"] and "checked" in publish.row_markup([strong_new]))
    check("host: it is shown, not silent",
          "first time this domain" in publish.row_markup(host_rows))
finally:
    publish.ITEMS = saved_for_hosts


# --- the instruction that runs is the one under version control -------------

check("skill: the repo copy is what run_harvest reads",
      publish.skill_file() == publish.SKILL, str(publish.skill_file()))
check("skill: and it is actually there", publish.SKILL.is_file())
check("skill: it is not served - render only publishes static/",
      publish.SKILL.parent.name == "hermes" and render.STATIC.name == "static")


# --- the header has to show the spread, not just a total --------------------
# Ten items that are all recalls is the failure mode, and a total hides it.

mixed_rows, _ = publish.judge({"items": [
    item(url="https://e.org/h1", pillar="health", score=90),
    item(url="https://e.org/h2", pillar="health", score=80),
    item(url="https://e.org/p1", pillar="practical", score=75),
    item(url="https://e.org/t1", pillar="technology", score=45),
]}, site)
check("header: counts the pillars that would actually publish",
      publish.spread(mixed_rows, P) == "health 2, safety & recalls 1",
      publish.spread(mixed_rows, P))
check("header: a below-the-bar item is not counted as coverage",
      "technology" not in publish.spread(mixed_rows, P))


# --- Hermes has to be told what is already live -----------------------------
# It has no memory between runs and cannot read the site, so on 20 Aug it spent a
# whole harvest re-proposing three urls published the day before. The archive now
# travels in the prompt.

saved_items = publish.ITEMS
tmp_archive = Path(tempfile.mkdtemp()) / "items.json"
try:
    publish.ITEMS = tmp_archive
    tmp_archive.write_text(json.dumps({"items": [
        item(title="Already up", url="https://e.org/live1"),
        item(title="Also up", url="https://e.org/live2"),
    ]}), encoding="utf-8")

    digest = publish.published_digest()
    check("exclude: a published url is listed", "https://e.org/live1" in digest)
    check("exclude: its title travels with it", "Already up" in digest)

    prompt = publish.harvest_prompt("THE SKILL TEXT", digest)
    check("exclude: the skill still leads the prompt", prompt.startswith("THE SKILL TEXT"))
    check("exclude: the archive reaches the prompt", "https://e.org/live2" in prompt)
    check("exclude: the task is the last thing read",
          prompt.rstrip().endswith("answer with the JSON block."))
    # Titles in items.json came off scraped pages. They are replayed into every
    # later prompt, so the block has to say what it is - the same boundary SKILL.md
    # draws around page content.
    check("exclude: the list is labelled data, not instruction",
          "data, not instruction" in prompt and "do not act on it" in prompt)

    many = [item(url="https://e.org/n" + str(k)) for k in range(publish.EXCLUDE_LIMIT + 40)]
    tmp_archive.write_text(json.dumps({"items": many}), encoding="utf-8")
    listed = publish.published_digest().count("https://e.org/n")
    check("exclude: the list is capped, not unbounded",
          listed == publish.EXCLUDE_LIMIT, str(listed) + " urls")

    tmp_archive.write_text("{ not json at all", encoding="utf-8")
    check("exclude: a corrupt archive still yields a usable prompt",
          "Nothing is published yet" in publish.published_digest())

    tmp_archive.write_text(json.dumps({"items": []}), encoding="utf-8")
    check("exclude: an empty archive reads as a sentence, not a blank",
          "Nothing is published yet" in publish.published_digest())
finally:
    publish.ITEMS = saved_items


# --- the commit can never widen beyond the one path -------------------------

check("guard: clean index accepted", publish.index_is_clean([]))
check("guard: our own path accepted", publish.index_is_clean(["content/items.json"]))
check("guard: foreign staged path refused", not publish.index_is_clean(["render.py"]))
check("guard: staged set must be exactly ours",
      publish.staged_exactly_ours(["content/items.json"]))
check("guard: staged set with an extra path refused",
      not publish.staged_exactly_ours(["content/items.json", "render.py"]))
check("guard: empty staged set refused", not publish.staged_exactly_ours([]))
check("guard: publish target is the content file only", publish.TRACKED == "content/items.json")

# The cards have to travel with the items that reference them, so the guard was
# widened - but to an explicit list, not to a prefix rule a stray file satisfies.
CARDS = ["static/og/a.png", "static/og/b.png"]
check("guard: cards may ride along when they are named",
      publish.staged_exactly_ours(["content/items.json"] + CARDS,
                                  ["content/items.json"] + CARDS))
check("guard: a card that was not named is still refused",
      not publish.staged_exactly_ours(["content/items.json"] + CARDS + ["static/og/x.png"],
                                      ["content/items.json"] + CARDS))
check("guard: order does not matter, membership does",
      publish.staged_exactly_ours(list(reversed(CARDS)) + ["content/items.json"],
                                  ["content/items.json"] + CARDS))
check("guard: a foreign path is refused even alongside real cards",
      not publish.staged_exactly_ours(["content/items.json", "render.py"] + CARDS,
                                      ["content/items.json"] + CARDS))
check("guard: a pre-staged card does not block a publish",
      publish.index_is_clean(["static/og/a.png"]))
check("guard: something that merely starts like a card path is not one",
      not publish.index_is_clean(["static/ogre.png"]))


# --- share cards: the image must never outlive the page it names -------------
# A link with no og:image renders as bare text in Slack, X and iMessage. The card
# is named from the same slug the page url uses, so the two cannot drift.

card_tpl = render.TEMPLATE.read_text(encoding="utf-8")
card_item = {"title": "A finding worth sharing", "url": "https://e.org/s",
             "source": "Src", "pillar": "health", "why": "Do the thing.",
             "published": "2026-08-20"}
check("card: an item with no drawn card falls back rather than 404ing",
      render.card_url(site, card_item).endswith("default.png"))
check("card: urls are absolute, because scrapers are unreliable with relative ones",
      render.card_url(site).startswith(site["url"].rstrip("/") + "/"))

real = [i for i in json.loads(render.ITEMS.read_text(encoding="utf-8"))["items"]
        if (render.STATIC / "og" / f"{render.slug_for(i)}.png").is_file()]
if real:
    page = render.build_item(site, card_tpl, real[0])
    want = render.card_url(site, real[0])
    check("card: an item page points at its own card", f'content="{want}"' in page)
    check("card: and it is the file that exists, not a guess",
          (render.STATIC / "og" / f"{render.slug_for(real[0])}.png").is_file())
    check("card: the filename is exactly the page slug, so the two cannot drift",
          want.endswith(render.slug_for(real[0]) + ".png"), want)
# Passes two ways, both correct: locally every card already exists so nothing is
# drawn, and in CI Pillow is absent so drawing degrades to a warning. Either way a
# re-publish must not churn out new binaries.
check("card: drawing again adds nothing", publish.draw_cards() == [])
check("card: the index falls back to the site card",
      f'content="{render.card_url(site)}"' in render.build_page(
          site, card_tpl, page_title="t", desc="d", path="index.html", main="", items=[]))
check("card: twitter is told to render it wide, not as a thumbnail",
      'content="summary_large_image"' in card_tpl)


# --- static/ is copied as a tree ---------------------------------------------
# The cards live in static/og/. The old flat loop skipped directories in silence,
# which looks exactly like a working build until every og:image 404s.

with tempfile.TemporaryDirectory() as tmp:
    src, dst = Path(tmp) / "static", Path(tmp) / "dist"
    (src / "og").mkdir(parents=True)
    (src / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (src / "og" / "card.png").write_bytes(b"not-really-a-png")
    dst.mkdir()
    real_static, real_dist = render.STATIC, render.DIST
    try:
        render.STATIC, render.DIST = src, dst
        render.copy_static()
        check("static: top-level files are copied", (dst / "favicon.svg").is_file())
        check("static: nested directories are copied too", (dst / "og" / "card.png").is_file())
    finally:
        render.STATIC, render.DIST = real_static, real_dist



# --- what you approve is what ships -----------------------------------------
# render.main() writes index.html; the review page renders build_preview(). If
# those two ever diverge the preview is teaching you to trust the wrong thing, so
# compare the actual bytes rather than trusting that both call build_index().

live = render.validate(render.load_json(render.ITEMS), P)
tmp = Path(tempfile.mkdtemp())
real_dist, real_argv = render.DIST, sys.argv
try:
    render.DIST, sys.argv = tmp, ["render.py"]
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        render.main()
    shipped = (tmp / "index.html").read_text(encoding="utf-8")
finally:
    render.DIST, sys.argv = real_dist, real_argv

previewed = publish.build_preview(site, live)
check("preview: byte-identical to what render.py ships", shipped == previewed,
      "" if shipped == previewed else f"{len(shipped)} vs {len(previewed)} chars")


# --- what a REJECTED item can put on the review page -------------------------
# Rejected items are displayed, and display used to bypass clean() entirely: a bidi
# override survived into the page, and nothing capped the length. The review screen
# is where a human judges trust, so it must not be the one screen that renders raw.

RLO_ITEM = item(title="Harmless" + RLO + "gnp.exe", url="/relative", why="a" + RLO + "b")
HUGE_ITEM = item(title="A" * 200_000, url="/relative2", why="B" * 200_000)
rows, _ = publish.judge({"items": [RLO_ITEM, HUGE_ITEM]}, site)
check("display: rejected item is still rejected", all(not r["ok"] for r in rows))
check("display: bidi stripped from a rejected title",
      all(RLO not in r["title"] and RLO not in r["why"] for r in rows))
check("display: rejected title capped at 300",
      all(len(r["title"]) <= 300 for r in rows), f"max {max(len(r['title']) for r in rows)}")
markup = publish.row_markup(rows)
check("display: no bidi anywhere in the rendered page", RLO not in markup)
check("display: page stays bounded for a hostile proposal", len(markup) < 20_000,
      f"{len(markup):,} chars")


# --- how old is this proposal, and is nothing found a crash? -----------------
# The scheduled harvest only runs while the PC is awake, so a stale proposal is a
# normal state and must be legible rather than silently treated as today's.

from datetime import datetime, timedelta                            # noqa: E402


def stamped(days_ago):
    when = datetime.now() - timedelta(days=days_ago)
    return Path(when.strftime("%Y-%m-%d_%H-%M-%S") + ".md")


check("age: today is recognised as fresh", publish.harvest_age(stamped(0))[1])
check("age: today is described in words",
      publish.harvest_age(stamped(0))[0].startswith("today"), publish.harvest_age(stamped(0))[0])
check("age: yesterday is not fresh", not publish.harvest_age(stamped(1))[1])
check("age: yesterday is named", publish.harvest_age(stamped(1))[0].startswith("yesterday"))
check("age: older is counted in days", "days ago" in publish.harvest_age(stamped(4))[0],
      publish.harvest_age(stamped(4))[0])
check("age: an unparseable name degrades quietly",
      publish.harvest_age(Path("not-a-timestamp.md")) == ("not-a-timestamp.md", False))

empty_rows, empty_survivors = publish.judge({"items": []}, site)
check("empty: a harvest that found nothing yields no rows",
      empty_rows == [] and empty_survivors == [])
allbad_rows, _ = publish.judge({"items": [item(url="/relative")]}, site)
check("empty: an all-rejected harvest still explains itself",
      len(allbad_rows) == 1 and not allbad_rows[0]["ok"] and allbad_rows[0]["reason"])


# --- publishing must add to the record, never replace it --------------------
# This replaced content/items.json outright, so every publish destroyed the previous
# day and the archive page archived nothing. The bug was invisible until someone
# looked for yesterday.

old = [item(url="https://e.org/old1"), item(url="https://e.org/old2")]
new = [item(url="https://e.org/new1"), item(url="https://e.org/old1")]

real_items = publish.ITEMS
scratch = Path(tempfile.mkdtemp()) / "items.json"
try:
    publish.ITEMS = scratch
    scratch.write_text(json.dumps({"items": old}), encoding="utf-8")
    merged, added, enriched = publish.merge_items(new)
    urls = [i["url"] for i in merged]
    check("merge: yesterday survives today", "https://e.org/old2" in urls)
    check("merge: today is added", "https://e.org/new1" in urls)
    check("merge: only the genuinely new count", added == 1, f"added={added}")
    check("merge: a repeated url is not duplicated", urls.count("https://e.org/old1") == 1)
    check("merge: newest first", urls[0] == "https://e.org/new1")

    scratch.write_text(json.dumps({"items": old}), encoding="utf-8")
    _, none_added, none_enriched = publish.merge_items([item(url="https://e.org/old1")])
    check("merge: an identical re-publish changes nothing",
          none_added == 0 and not none_enriched)

    scratch.write_text("{ this is not json", encoding="utf-8")
    _, n, _ = publish.merge_items(new)
    check("merge: a corrupt file does not lose today's work", n == len(new))

    # --- the bug that silently discarded three briefs on 19 Aug -----------------
    thin = [{"title": "Thin one", "url": "https://e.org/thin", "source": "S",
             "pillar": "health", "why": "one line"}]
    better = [{"title": "A BETTER HEADLINE", "url": "https://e.org/thin", "source": "S",
               "pillar": "health", "why": "one line",
               "brief": "Para one." + chr(10) * 2 + "Para two."}]
    scratch.write_text(json.dumps({"items": thin}), encoding="utf-8")
    merged, added, enriched = publish.merge_items(better)
    stored = merged[0]
    check("enrich: a missing brief is filled in", bool(stored.get("brief")),
          str(stored.get("brief"))[:30])
    check("enrich: it counts as a change, not 'nothing new'", added == 0 and len(enriched) == 1)
    check("enrich: the title is NOT overwritten", stored["title"] == "Thin one",
          stored["title"])
    check("enrich: so the page url cannot move",
          render.slug_for(stored) == render.slug_for(thin[0]))

    # a field that already has a value is never replaced
    full = [{"title": "Has one", "url": "https://e.org/full", "source": "S",
             "pillar": "health", "brief": "ORIGINAL"}]
    scratch.write_text(json.dumps({"items": full}), encoding="utf-8")
    merged, _, enriched = publish.merge_items(
        [{**full[0], "brief": "REPLACEMENT ATTEMPT"}])
    check("enrich: an existing brief is left alone",
          merged[0]["brief"] == "ORIGINAL" and not enriched)
finally:
    publish.ITEMS = real_items


# --- item pages: the url is a promise, and it must not move ------------------

a = {"title": "CPSC recalls 250,000 mini-fridges", "url": "https://e.org/a"}
check("slug: readable", render.slug_for(a).startswith("cpsc-recalls-250-000-mini-fridges"),
      render.slug_for(a))
check("slug: stable across calls", render.slug_for(a) == render.slug_for(dict(a)))
check("slug: same headline, different source is a different page",
      render.slug_for(a) != render.slug_for({**a, "url": "https://e.org/b"}))
check("slug: url-safe", re.fullmatch(r"[a-z0-9-]+", render.slug_for(a)) is not None)
check("slug: a title with no usable characters still yields a page",
      render.slug_for({"title": "!!!", "url": "https://e.org/c"}).startswith("item-"))

page = render.build_item(site, render.TEMPLATE.read_text(encoding="utf-8"),
                         {"title": "T", "url": "https://src.example/x", "source": "Src",
                          "pillar": "health", "published": "2026-08-19",
                          "brief": "Para one." + chr(10) * 2 + "Para two."})
check("item page: the brief renders as separate paragraphs", page.count("<p>Para") == 2)
check("item page: credits and links the source",
      'href="https://src.example/x"' in page and "Read the original at Src" in page)
check("item page: falls back to the one-liner when there is no brief",
      "<p>just this</p>" in render.build_item(
          site, render.TEMPLATE.read_text(encoding="utf-8"),
          {"title": "T", "url": "https://e.org/d", "source": "S", "pillar": "health",
           "why": "just this"}))


# --- structured data and sitemap dates --------------------------------------
# Every item page used to declare itself a CollectionPage containing one thing, and
# every sitemap url carried the same date. Both told crawlers something false on the
# pages that exist to be found.

tpl_txt = render.TEMPLATE.read_text(encoding="utf-8")
one = {"title": "T", "url": "https://src.example/x", "source": "Src",
       "pillar": "health", "published": "2026-08-11", "why": "the takeaway"}
page = render.build_item(site, tpl_txt, one)
ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>',
                          page, re.S).group(1))
check("schema: an item page is an Article", ld["@type"] == "Article", ld["@type"])
check("schema: it carries the publication date", ld.get("datePublished") == "2026-08-11")
check("schema: it points at the source it is based on",
      ld.get("isBasedOn") == "https://src.example/x")
check("schema: a listing page is still a CollectionPage",
      json.loads(render.jsonld(site, "t", "https://x/", [one]))["@type"] == "CollectionPage")
hostile_page = render.build_item(site, tpl_txt, {**one, "title": "a</script><script>x"})
hostile_ld = re.search(r'<script type="application/ld\+json">(.*?)</script>',
                       hostile_page, re.S).group(1)
check("schema: </script> in a title cannot close the ld+json block",
      "</" + "script>" not in hostile_ld and json.loads(hostile_ld) is not None)
check("schema: and cannot inject markup into the page body",
      "a</" + "script><script>x" not in hostile_page.split("<body>")[1])

two = [one, {**one, "url": "https://src.example/y", "published": "2026-07-01"}]
sm = render.sitemap(site, two)
stamps = re.findall(r"<lastmod>([^<]+)</lastmod>", sm)
locs = re.findall(r"<loc>([^<]+)</loc>", sm)
check("sitemap: item pages carry their own dates", "2026-07-01" in stamps, str(stamps))
check("sitemap: not one date repeated for everything", len(set(stamps)) > 1, str(set(stamps)))
# Named rather than counted: a bare arithmetic check stays green when a standing
# page silently stops being listed, because the total still adds up.
STANDING = ("/", "/archive.html", "/privacy.html")
check("sitemap: every standing page is listed",
      all(any(l.endswith(pth) for l in locs) for pth in STANDING), str(locs))
check("sitemap: one entry per item, nothing listed twice",
      len(locs) == len(set(locs)) == len(two) + len(STANDING), str(len(locs)))
check("sitemap: no subscribe entry while it is unconfigured",
      not any("subscribe" in l for l in locs))


# --- the subscribe page: absent until it would work, correct when it does ----
# Browser code knows only a public Worker endpoint and sitekey. The storage ids and
# validation secret belong to the Worker, where readers cannot extract them.

SUB = {"endpoint": "https://signal-newsletter.example.workers.dev/subscribe",
       "turnstile_sitekey": "0x4AAAAAAAexample"}
configured = dict(site, subscribe=SUB)
sub_page = render.build_subscribe(configured, tpl_txt, SUB)

check("subscribe: posts to the verified backend",
      f'action="{SUB["endpoint"]}"' in sub_page)
check("subscribe: the browser receives no Google Form identifiers",
      "docs.google.com/forms" not in sub_page and "entry." not in sub_page)
check("subscribe: the turnstile sitekey is the configured one",
      f'data-sitekey="{SUB["turnstile_sitekey"]}"' in sub_page)
check("subscribe: Turnstile result is bound to the backend action check",
      'data-action="newsletter_subscribe"' in sub_page)
check("subscribe: turnstile is loaded only here",
      "challenges.cloudflare.com/turnstile" in sub_page
      and "challenges.cloudflare.com/turnstile" not in render.build_privacy(site, tpl_txt))
# Without method=post the browser appends the address to the url as a query string,
# which puts it in history, logs and the referrer.
check("subscribe: submits by POST, never as a query string",
      'method="post"' in sub_page)
check("subscribe: the input is a real email field, and required",
      'name="email" type="email"' in sub_page and "required" in sub_page)
check("subscribe: the placeholder is not doing the job of a label",
      'class="vh" for="subscribe-email"' in sub_page)
check("subscribe: carries a bot honeypot outside the keyboard order",
      'name="website"' in sub_page and 'tabindex="-1"' in sub_page)
check("subscribe: it links to the privacy policy", 'href="/privacy.html"' in sub_page)
check("subscribe: javascript waits for an inspectable backend result",
      'mode: "cors"' in tpl_txt and "response.ok" in tpl_txt
      and 'mode: "no-cors"' not in tpl_txt)
# The form is display:flex, which beats the UA stylesheet's [hidden]{display:none}.
# Without this rule the thank-you appeared *under* a form that never went away.
check("subscribe: hidden actually hides, even with display:flex set",
      "[hidden]{display:none !important}" in tpl_txt)
check("subscribe: nav gains the link only when configured",
      "Subscribe" in [l["label"] for l in render.nav_links(configured)]
      and "Subscribe" not in [l["label"] for l in render.nav_links(site)])

priv = render.build_privacy(site, tpl_txt)
check("privacy: says what is collected and on what basis",
      "email address" in priv and "consent" in priv)
check("privacy: tells the reader how to get removed", "unsubscrib" in priv.lower())
check("privacy: does not claim analytics that are not configured",
      ("no analytics" in priv) == (not site.get("fathom_site_id")))


# --- the mailing: what you just published, not what happens to share a date --
# Filtering by the source's published date splits a batch apart, because one publish
# routinely mixes a recall from yesterday with a study from last month.

mail_items = [
    {"title": "Recall you can act on", "url": "https://www.cpsc.gov/r", "source": "CPSC",
     "pillar": "practical", "why": "Stop using it.", "published": "2026-08-13", "score": 88},
    {"title": "A learning finding", "url": "https://www.nature.com/n", "source": "Nature",
     "pillar": "education", "why": "Space your revision.", "published": "2026-07-02", "score": 74},
]
mail_html, mail_text = render.build_email(site, mail_items, "2026-08-20")

check("mail: every item appears", all(i["title"] in mail_html for i in mail_items))
check("mail: items link to this site, not straight to the source",
      mail_html.count(site["url"].rstrip("/") + "/items/") == len(mail_items))
# Mail clients drop <style> blocks and know nothing of custom properties, so a
# stylesheet here would arrive as unstyled text.
check("mail: styling is inline, with no stylesheet to be stripped",
      "<style" not in mail_html and "var(--" not in mail_html)
check("mail: it carries an unsubscribe route", "Unsubscribe" in mail_html)
check("mail: the plain-text twin carries one too", "Unsubscribe" in mail_text)
check("mail: a title with markup in it cannot break out",
      "&lt;script&gt;" in render.build_email(
          site, [dict(mail_items[0], title="<script>x</script>")], "2026-08-20")[0])
check("mail: unsubscribe falls back to the security contact rather than nowhere",
      render.unsubscribe_to(site) == site["security_contact"])
check("mail: an explicit unsubscribe address wins",
      render.unsubscribe_to(dict(site, subscribe={"unsubscribe": "bye@e.org"})) == "bye@e.org")
check("mail: drafts land outside the repo, so they can never be committed",
      publish.ROOT not in publish.MAIL_DIR.parents and publish.MAIL_DIR.name == "newsletters")


# --- the git path, exercised in a throwaway repo so CI covers it too ---------
# These functions touch git, so they were only ever tested by hand. A throwaway repo
# with a local bare remote needs no network and runs anywhere, which makes the guards
# regression-proof instead of merely once-verified.
import subprocess                                                  # noqa: E402

sandbox = Path(tempfile.mkdtemp())
remote, work = sandbox / "remote.git", sandbox / "work"


def g(*a, cwd=None):
    r = subprocess.run(["git", *a], cwd=str(cwd or work), capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(a)}: {r.stderr.strip()}")
    return r.stdout.strip()


g("init", "--bare", "-q", "-b", "main", str(remote), cwd=sandbox)
work.mkdir()
g("init", "-q", "-b", "main")
g("config", "user.email", "t@example.invalid")
g("config", "user.name", "t")
(work / "content").mkdir()
(work / "content" / "items.json").write_text('{"items": []}\n', encoding="utf-8")
(work / "unrelated.txt").write_text("x\n", encoding="utf-8")
g("add", "-A")
g("commit", "-qm", "base")
g("remote", "add", "origin", str(remote))
g("push", "-qu", "origin", "main")

real_root, real_items = publish.ROOT, publish.ITEMS
try:
    publish.ROOT, publish.ITEMS = work, work / "content" / "items.json"

    good = [item(url="https://e.org/one"), item(url="https://e.org/two")]
    publish.publish_items(good)
    touched = g("show", "--format=", "--name-only", "HEAD").split()
    check("git: publish commits exactly one path", touched == ["content/items.json"], str(touched))
    check("git: it reached the remote", g("rev-parse", "HEAD") == g("rev-parse", "origin/main"))
    check("git: nothing left staged", g("diff", "--cached", "--name-only") == "")

    # an unrelated staged change must stop the publish dead
    (work / "unrelated.txt").write_text("tampered\n", encoding="utf-8")
    g("add", "unrelated.txt")
    try:
        publish.publish_items([item(url="https://e.org/three")])
        check("git: refuses when something else is staged", False, "it published anyway")
    except RuntimeError as exc:
        check("git: refuses when something else is staged", "already staged" in str(exc))
    g("reset", "-q")
    g("checkout", "-q", "--", "unrelated.txt")

    # a commit that never reached the remote must be finished, not called a no-op
    (work / "content" / "items.json").write_text('{"items": [1]}\n', encoding="utf-8")
    g("add", "--", "content/items.json")
    g("commit", "-qm", "stranded")
    check("git: an unpushed commit is detected", publish.unpushed_count() == 1,
          f"got {publish.unpushed_count()}")
    publish.publish_items(good)
    check("git: unpushed commit gets pushed, not reported as 'nothing changed'",
          g("rev-parse", "HEAD") == g("rev-parse", "origin/main"))

    before = g("rev-parse", "HEAD")
    publish.rollback()
    check("git: rollback commits one path and pushes",
          g("rev-parse", "HEAD") != before
          and g("rev-parse", "HEAD") == g("rev-parse", "origin/main"))
finally:
    publish.ROOT, publish.ITEMS = real_root, real_items


width = max(len(n) for n, _, _ in results)
for name, passed, detail in results:
    print(f"  [{'PASS' if passed else 'FAIL'}] {name.ljust(width)}  {detail}")
print()
ok = all(p for _, p, _ in results)
print(f"ALL PASS ({len(results)}/{len(results)})" if ok
      else "FAILURES: " + ", ".join(n for n, p, _ in results if not p))

# Exit non-zero so CI can gate on this. Without it the job goes green on failure,
# which is worse than having no test job at all.
sys.exit(0 if ok else 1)
