"""Probes for publish.py — the gate between Hermes' proposal and the live site.

Everything here is pure or in-memory: no git, no network, no server. The guards
that matter for committing are written as pure functions precisely so they can be
tested without a repository. Run from the repo root.
"""
import contextlib, io, json, sys, tempfile
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
