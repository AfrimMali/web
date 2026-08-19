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
