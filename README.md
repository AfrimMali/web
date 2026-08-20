# Signal — the website

Milestone 1: a working, deployable static site. No bundler, no framework, no npm,
no dependencies. One Python file reads `content/items.json` and writes `dist/`.

```
site.json           title, colours of the copy, pillars, Fathom id
content/items.json  ← the only thing Hermes needs to write
templates/base.html the entire design, one file
render.py           builds dist/ (stdlib only)
```

## Run it

```bash
python render.py --serve      # builds, serves http://localhost:8000
```

That's the whole toolchain. There is nothing to install.

Two suites cover the sanitiser and the generated CSP. Each prints its own pass
count, and CI runs both and refuses to publish if either fails:

```bash
python tests/audit_check.py
python tests/sec_check.py
```

Before deploying, set `url` in `site.json` to your real domain — canonical tags,
the sitemap and the feed all derive from it.

## Deploy

Push to GitHub, then Settings → Pages → Source: **GitHub Actions**. The workflow
builds and publishes on every push and once a day at 06:00 UTC.

For a custom domain, add it under Settings → Pages and put the same value in
`site.json`. A real domain matters more for search visibility than anything in the
markup.

## The daily loop

**Nothing runs in the background and nothing is scheduled.** You start it, it finishes,
it exits.

Double-click the **Signal** shortcut on the desktop (or run `python publish.py`). It hands
the harvest instruction to Hermes as a single one-shot run, waits for the answer — five to
ten minutes for a full ten-item sweep — and shows it to you. Hermes has one capability, web search and extraction:
no shell, no file access, no memory, no ability to edit its own instruction. It cannot
publish; it can only propose. What you get:

- it reads the newest proposal and pulls the JSON out of whatever prose surrounds it
- every item is shown as it will appear, with its score, its claimed source, and the
  **real domain** of the link in punycode — a Cyrillic `сochrane.org` is pixel-identical
  to the real thing in a browser, and this is where you would catch it
- anything `--validate` rejects is greyed out with the reason
- anything scoring below the publish bar is shown **unticked** with "below the bar" against
  it — visible, and publishable if you tick it, but never leading the site by default
- any link to a domain **this site has never linked before** is flagged. Hermes reads pages
  an attacker can write, and it cannot publish anything itself, so the exposure is a
  proposal carrying a plausible source name and a hostile link. `fda.gov` goes familiar
  fast; a stranger does not. It warns, never blocks
- untick anything you dislike; the preview on the right is the actual page
- **Publish** writes `content/items.json`, commits that one path and pushes. Nothing else
  is ever staged, and it refuses outright if anything else is already staged.
- **Roll back** restores the previously published content the same way

The review page binds to loopback only and needs a session token, because a process that
can publish to a live site should not be reachable by a page you happen to have open.

The header counts what would publish and names the spread across pillars — "8 of 12
ticked, 2 below the bar — health 3, learning 2, technology 1, safety & recalls 2" — because
ten items that are all recalls is the failure worth catching, and a total hides it. The
subtitle tells you when the harvest ran — "today, 09:04" — because a stale proposal
reads differently from a fresh one. A harvest that found nothing is reported as a normal
day rather than an error; most things not clearing the bar is the whole premise.

`publish.py` works on any proposal file — `--source FILE` — so the review-and-publish half
stands on its own even if the harvester is not running. `--no-harvest` shows only what is
already there; `--harvest` forces a fresh run even when today already has one, which is
what you want straight after editing `SKILL.md`.

### Before the harvester can run

The Hermes side needs two things that are account matters, not configuration:

- **credit with the model provider.** A harvest fails with `HTTP 402 Insufficient
  Balance` until the DeepSeek account has credit.
- **a web-search backend.** `web_search` needs one of `TAVILY_API_KEY`,
  `BRAVE_SEARCH_API_KEY`, `EXA_API_KEY` or `FIRECRAWL_API_KEY`. With none set the toolset
  is enabled but unavailable, and the job would run with no tools at all.

### Tuning what gets harvested

The instruction lives at **`hermes/SKILL.md`, in this repo** — that is the copy
`publish.py` actually reads, so retuning it leaves a diff you can go back and read. A copy
under `%LOCALAPPDATA%\hermes\profiles\signal\...` still works as a fallback for an older
checkout, and every harvest prints which of the two it used, so the pair can never quietly
disagree. It is plain English and meant to be edited, and it is the whole of the research
behaviour — there is no scheduler config and no job settings anywhere else.

What it asks for: **ten items covering all four pillars**, scored on whether an ordinary
reader can act on it themselves, today, without a gatekeeper — which is why a recall
outranks a first-in-class drug approval that nobody reading can obtain. Sources run well
past the recall feeds: journals and their open news pages, public datasets, and the wider
set of regulators. Paywalled journals get one extra rule, because `web_extract` usually
returns only an abstract: say so in the brief, and invent nothing beyond what was on the
page.

Scores are **within a pillar, not across them**. A recall is the only kind of finding
where "can they act today without asking anyone" is always yes, so one absolute scale
turns the site into a recall feed — the 20 Aug run produced seven recalls in eight
publishable items while four real health and learning findings sat at 62–68. Each pillar
now has its own anchors in `SKILL.md`, so an 80 in learning means "near the top of what
learning research offers", not "as useful as an 80 in safety". The index groups by pillar,
so items are only ever ordered against their own kind.

**Two thresholds, and they must stay in step:**

| Threshold | Where | What it does |
|---|---|---|
| Return floor, 40 | `SKILL.md` | below this an item is dropped silently and never appears |
| Publish bar, 70 | `SKILL.md`, and `PUBLISH_SCORE` in `publish.py` | at or above it an item arrives ticked; below it, shown but unticked |

The dial for volume is the recency line in `SKILL.md` — currently seven days. A harvest
runs daily, so only about one day of material is genuinely new each morning; if runs come
back thin, that line is the first thing to change.

**The archive travels in the prompt.** `publish.py` appends every published URL and title
(newest `EXCLUDE_LIMIT` of them) so Hermes knows what not to propose. Without it, it
re-finds the same top stories every day: on 20 Aug three of five items were URLs published
the day before.

Harvests are kept in `../harvests/` beside the repo, last 50, never committed.

## Email subscription

`/subscribe.html` collects addresses through a Google Form, because a static site on
Pages has nowhere to receive a POST. **The page does not exist until all three values in
`site.json` are filled in** — `form_id`, `entry_id` and `turnstile_sitekey` — and while
they are blank there is no page, no nav link, and every page's CSP is untouched. Half a
config would put a dead form on the site, so it counts as none.

Where to get them: `form_id` is the `1FAIpQLS…` string in the form's URL; `entry_id`
comes from the form's *Get prefilled link* (fill anything, copy the link, read the
`entry.NNNNNNN` out of it); `turnstile_sitekey` is the **public** Cloudflare key — the
secret one has no use here and must never be committed.

The form keeps a real `action` and `method`, so with JavaScript off it posts straight to
Google and Google confirms it. With JavaScript on it posts in the background and swaps in
a thank-you instead, which keeps the reader here — at a price worth knowing: **Google
sends no CORS headers, so the page cannot tell success from failure.** A rejected request
is handed back to the plain POST rather than swallowed, but an accepted-looking one proves
nothing.

**Turnstile is a gate on this form, not on the endpoint.** Nothing verifies the token —
that needs a server call to Cloudflare, and Google Forms will not make one. It stops bots
driving this page; it does nothing about a direct POST to the Google URL, which is public
and sits in this page's source. Since sending is manual, reading the list before you send
is the control that actually works.

Only `subscribe.html` gets the four CSP additions it needs (Turnstile's script and frame,
Google's form-action and connect-src). Every other page keeps `form-action 'none'`,
`connect-src 'none'`, and no third-party JavaScript at all. `tests/sec_check.py` holds
that line.

### Sending

Nothing is sent automatically and there is no mail credential in this repo.

    python publish.py --newsletter              the items added by the last publish
    python publish.py --newsletter 2026-08-20   everything with that published date

It writes an HTML mail and a plain-text twin to `../newsletters/`, beside the harvests and
outside the repo. You export the list from the sheet, paste the HTML in, and send it
yourself. The default is derived from git — the items that appeared in the most recent
commit to `content/items.json` — because "what I just published" is not the same question
as "what shares today's date": a batch routinely mixes a recall from yesterday with a
study from last month.

Every mailing carries an unsubscribe address (`subscribe.unsubscribe`, falling back to
`security_contact`). `--newsletter` warns loudly if neither is set, because a bulk mail
without one should not go out.

`/privacy.html` is built unconditionally and says what is collected, why, on what basis,
and how to be removed. While `fathom_site_id` is blank it also states outright that there
is no analytics and no cookies — that sentence is the first thing that has to change if
analytics is ever switched on.

## Share cards

A link with no `og:image` renders as bare text in Slack, WhatsApp, X and iMessage, so every
item gets a 1200×630 card carrying its headline.

    python tools/make_cards.py            draw whatever is missing
    python tools/make_cards.py --force    redraw everything

Cards are written to `static/og/{slug}.png`, named from the same `render.slug_for()` the
page URL uses, so the image and the page it belongs to cannot drift apart. An item with no
card falls back to `og/default.png` rather than pointing at a file that is not there.

**This cannot run in CI and is not meant to.** Drawing text needs Pillow, and the workflow
is deliberately standard-library only, so the cards are drawn here and committed.
`publish.py` shells out to the same script when you publish and includes any new cards in
the commit — if Pillow is missing it prints a warning and publishes without them, because a
missing preview image must never hold up a recall notice.

The commit guard was **generalised, not relaxed**: `commit_tracked_only()` takes the
explicit list of paths it may commit — `content/items.json` plus exactly the cards just
drawn — and still refuses if the staged set differs by one entry. A prefix rule like
"anything under static/og/" would be satisfied by a stray file; a list is not.

`tools/Newsreader.ttf` is the unmodified variable font from Google Fonts, with its `OFL.txt`
alongside. The generator picks the weight through the font's own axes rather than shipping
an instanced copy, which keeps the file redistributable without argument.

## The contract with Hermes

The site reads exactly one file. Hermes writes it, `render.py` renders it, and
neither knows the other exists. If your scraper breaks, the last good
`items.json` is still on disk and the site still builds.

```json
{
  "generated_at": "2026-08-18T06:00:00Z",
  "items": [
    {
      "title":     "required — the headline, plain text",
      "url":       "required — absolute https link to the source",
      "source":    "required — publication name, e.g. 'Cochrane'",
      "pillar":    "required — one of: health, education, technology, practical",
      "why":       "optional — one sentence, the takeaway itself, not a teaser",
      "published": "optional — YYYY-MM-DD",
      "score":     "optional — 0-100, used for ordering"
    }
  ]
}
```

Check Hermes' output before trusting it:

```bash
python render.py --validate
```

It rejects items missing required fields, items with a relative URL, items with an
unrecognised pillar, and duplicate URLs — reporting each one — then tells you how
many survived. Malformed entries are skipped, never rendered.

`pillar` values must match the keys in `site.json`. Rename or add pillars there and
the section headings follow.

## What I deliberately left out

You sent a teardown of darioamodei.com listing Rspack, jQuery 3.5.1, `webpackChunk`
code splitting, `.col-xs-*` grids and Phosphor Icons. That site is built on Webflow
(its assets come from `cdn.prod.website-files.com`), and every one of those is
Webflow's stock runtime, shipped identically on every site it publishes. None of it
is a design decision, and none of it produces the look.

Specifically not copied:

- **SPA routing.** Client-rendered content lands in a slower, less reliable crawl
  queue. You asked for this to be findable; static HTML is indexed on the first
  pass. This site has 31 lines of JavaScript and works with JS switched off.
- **jQuery.** 30KB for DOM work the browser does natively.
- **Rspack / chunking.** A build pipeline for an app. Your page is a list of links.
- **Phosphor Icons.** The design uses no icons.

Kept: the Jamstack/CDN model (that's what GitHub Pages is), and Fathom — put your
site ID in `site.json` and it loads with `honor-dnt` on. Leave it blank and no
third-party script is emitted at all.

## What "searchable" realistically means here

The site ships the full package: server-rendered HTML, canonical tags, OpenGraph
and Twitter cards, `CollectionPage`/`ItemList` JSON-LD, `sitemap.xml`, `robots.txt`
and an Atom feed. It will index cleanly and score well on Core Web Vitals.

Being honest about the ceiling: a page of links to other people's work doesn't rank
on its own merits, because the ranking value sits with the sources you link to.
What actually makes an aggregator findable is the feed, the domain, and people
linking to it. If you later want search traffic, the lever is original writing —
a weekly note explaining what the week's findings add up to — not more markup.

## Design

Colours are five CSS variables at the top of `templates/base.html`, one set for
light and one for `.dark`. These are now the exact values sampled from
darioamodei.com's stylesheet — ivory-medium ground, slate-dark text, slate-light
muted, cloud-light rules. That site has no dark mode, so the dark set reuses the
same swatches inverted. Both pass WCAG AA. Nothing else depends on them.

Type is Newsreader, which is what the reference uses too. Body is 20px Regular at
the 24pt optical cut; the masthead uses the 60pt cut at 700. The optical sizes come
from the `opsz` axis on the Google Fonts variable face, not from the reference's
own font files.

The theme is set before first paint, so there's no flash. It follows the system
setting until you click the toggle, then remembers your choice in `localStorage`.
