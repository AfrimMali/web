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

`/subscribe.html` is a static frontend backed by the Worker in `worker/`. The browser
sends the email address and Turnstile token to that Worker. The Worker verifies the token
with Cloudflare, checks its `hostname` and `action`, and only then submits the address to
the Google Form. A success message therefore means both checks completed; the old opaque
Google request, which could report success without knowing whether anything was stored,
is gone.

The generated page only contains two public values: the Worker `endpoint` and Turnstile
`sitekey`. Put them in `site.json` for a local build, or set the repository Actions
variables `SUBSCRIBE_ENDPOINT` and `TURNSTILE_SITEKEY` for production. Until both exist,
the generator intentionally emits no page or navigation link rather than publishing a
dead form.

The private values live in Cloudflare Worker secrets, never in this public repository:

- `TURNSTILE_SECRET_KEY` — the secret paired with the public widget sitekey.
- `GOOGLE_FORM_ID` — the `1FAIpQLS…` part of the Form URL.
- `GOOGLE_ENTRY_ID` — the email question's `entry.NNNNNNN` name, obtained from a
  prefilled Form link.

To activate it:

1. Create a Google Form with one required email question and obtain its form and entry
   ids.
2. Create a Turnstile widget restricted to `afrimmali.com`; keep its secret private.
3. Deploy the Worker and set its secrets. `wrangler.toml` already carries
   `ALLOWED_ORIGIN` and `TURNSTILE_HOSTNAME`, so `deploy` sets those for you; the three
   secrets are separate and must be set after the first deploy — wrangler has no
   `[secrets]` section, and nothing declares them for you:

       cd worker
       npx wrangler login
       npx wrangler deploy
       npx wrangler secret put TURNSTILE_SECRET_KEY
       npx wrangler secret put GOOGLE_FORM_ID
       npx wrangler secret put GOOGLE_ENTRY_ID

   Then check it before touching the site: `curl https://…workers.dev/health` returns
   **200 `ready`** when all four values are present and **503** when any is missing. That
   endpoint is the configuration test — use it rather than guessing from a failed signup.
4. Add the deployed Worker URL and public sitekey as the two GitHub Actions variables,
   then run the `publish` workflow. The next build emits `/subscribe.html`.
5. Create and test the `unsubscribe@afrimmali.com` forward, then set
   `subscribe.unsubscribe` to that address before sending a mailing.

**Testing locally will fail the hostname check, and that is the Worker working.** It
requires Turnstile's reported `hostname` to equal `TURNSTILE_HOSTNAME`, which is
`afrimmali.com`. A widget solved on `localhost` reports `localhost` and is rejected. Test
against production, or run a dev Worker with `TURNSTILE_HOSTNAME=localhost`.

**On what is actually secret.** `TURNSTILE_SECRET_KEY` is: if it leaks, the bot protection
is defeated and it must be rotated at once. The two Google ids are not, in the same sense —
whoever has them can add rows to a form they cannot read, which is spam in a sheet you
already review before sending. Keeping them in the Worker is still right, because it stops
anyone reading the page from posting past Turnstile straight to Google. Knowing which is
which decides how hard you react if one ever turns up in a log.

Only `subscribe.html` gets the CSP additions for Turnstile and the exact Worker origin.
Every other page keeps `form-action 'none'`, `connect-src 'none'`, and no third-party
JavaScript. `tests/sec_check.py` holds that boundary; `worker/test/subscribe.test.mjs`
covers origin rejection, input validation, the honeypot, Turnstile checks, Google failure,
and the no-JavaScript response.

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

### Account and domain hardening

These controls are important launch work, but they do not determine whether
`subscribe.html` is generated and cannot be implemented in source code:

- Enable Porkbun 2FA and the domain transfer lock in the registrar account.
- Verify `afrimmali.com` in the GitHub account/organization that owns the Pages site.
- Review and revoke unused GitHub personal access tokens and OAuth app grants.
- Protect `main` with **Restrict deletions** and **Block force pushes**, and nothing
  else. Do **not** require a pull request, a status check, an up-to-date branch, or apply
  rules to administrators: `publish.py` pushes straight to `main`, and every one of those
  settings blocks a direct push. The failure would surface mid-publish, with a recall
  notice half-shipped. Be honest about what this buys — it stops history being rewritten
  or the branch deleted; it does not stop a malicious push, and nothing will while
  publishing is a direct push. 2FA is the control doing that work.

Treat these as account-owner checks: verify each in its provider dashboard rather than
recording an unverified claim in the repository.

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
