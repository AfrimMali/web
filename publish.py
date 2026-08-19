#!/usr/bin/env python3
"""publish.py — review what Hermes proposed, then publish it in one click.

Hermes runs on a schedule with a single toolset (web search and extraction) and
no way to write anything at all. Its answer is saved by its own cron runtime.
This script reads that answer, shows every item exactly as it will appear on the
site, and on confirmation writes content/items.json, commits that one path and
pushes. Nothing else is ever committed.

    python publish.py            open the review page
    python publish.py --check    print the newest proposal and exit, no server
    python publish.py --source F read F instead of the newest Hermes run

The server binds loopback only and requires a session token, because a process
that can publish to a live website should not be reachable by a page you happen
to have open in another tab.
"""

import argparse
import contextlib
import hashlib
import html
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import render

ROOT = Path(__file__).parent
ITEMS = ROOT / "content" / "items.json"
ADMIN_TEMPLATE = ROOT / "templates" / "admin.html"

# The one path this script is ever allowed to commit.
TRACKED = "content/items.json"

PORT = 8765
IDLE_SECONDS = 30 * 60

PROFILE = "signal"                 # the Hermes profile that owns the harvest job
JOB = "signal-harvest"
HARVEST_TIMEOUT = 15 * 60

# The server is threaded, so two requests can arrive at once. The button disables
# itself after a click, but that is the browser's promise, not ours - a second tab
# holding the same token would otherwise race this one through the git index.
PUBLISH_LOCK = threading.Lock()


# ---------- finding what Hermes said ----------

def hermes_home():
    """Hermes' data directory.

    Not ~/.hermes on this platform: the Windows installer puts it under
    LOCALAPPDATA, so probe rather than assume. HERMES_HOME wins when set.
    """
    if os.environ.get("HERMES_HOME"):
        return Path(os.environ["HERMES_HOME"])
    local = os.environ.get("LOCALAPPDATA")
    candidates = [Path(local) / "hermes"] if local else []
    candidates.append(Path.home() / ".hermes")
    return next((c for c in candidates if c.is_dir()), candidates[-1])


def hermes_output_dirs():
    """Where Hermes' cron writes each run's answer.

    The cron runtime writes these files, not the model - the agent is configured
    with no file tool at all. The `signal` profile is preferred; the default
    profile is a fallback so this still works before that profile exists.
    """
    home = hermes_home()
    return [home / "profiles" / "signal" / "cron" / "output", home / "cron" / "output"]


def newest_output():
    """Newest run-output file, or None. Names are %Y-%m-%d_%H-%M-%S.md, so a
    reverse lexical sort is a reverse chronological one."""
    found = []
    for base in hermes_output_dirs():
        if base.is_dir():
            found += [f for f in base.glob("*/*.md") if f.is_file()]
    return max(found, key=lambda f: f.name, default=None)


def harvest_age(path):
    """(human description, is_from_today) for a run-output file.

    The age of a proposal changes how you read it, so the review page says when the
    harvest ran rather than showing a path nobody can parse at a glance.
    """
    try:
        when = datetime.strptime(path.stem, "%Y-%m-%d_%H-%M-%S")
    except ValueError:
        return path.name, False
    today = datetime.now().date()
    days = (today - when.date()).days
    if days == 0:
        return f"today, {when:%H:%M}", True
    if days == 1:
        return f"yesterday, {when:%H:%M}", False
    return f"{days} days ago ({when:%d %b, %H:%M})", False


def run_harvest():
    """Ask Hermes to run the harvest now.

    The scheduled job only fires while the PC is awake, so a missed 09:00 would
    otherwise leave you with nothing and no way forward. Running on demand makes the
    schedule an optimisation rather than a dependency.
    """
    exe = shutil.which("hermes")
    if not exe:
        return False, "hermes is not on PATH, so I cannot start a harvest from here."
    print("  no harvest yet today - running one now, this usually takes a minute or two")
    try:
        r = subprocess.run([exe, "-p", PROFILE, "cron", "run", JOB],
                           capture_output=True, text=True, encoding="utf-8",
                           timeout=HARVEST_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, f"the harvest did not finish within {HARVEST_TIMEOUT // 60} minutes."
    out = (r.stdout or "") + (r.stderr or "")
    if "Insufficient Balance" in out:
        return False, "the model provider reports no credit - top up and try again."
    if r.returncode != 0 and "failed" in out.lower():
        tail = [l for l in out.splitlines() if l.strip()][-1:] or [""]
        return False, f"the harvest failed: {tail[0].strip()[:160]}"
    return True, ""


def response_section(text):
    """Just the model's answer, discarding the prompt the cron runtime records above it.

    The run-output file is "# Cron Job / ## Prompt / ## Response", and the prompt
    contains the whole skill - including a worked JSON schema example and the literal
    characters that open a fenced block. Parsing the file as a whole risks lifting
    that example and offering "required - the headline, plain text" as a real item.
    Only ever read what came after the answer began.
    """
    marker = re.search(r"^##\s+Response\s*$", text, re.M)
    return text[marker.end():] if marker else text


def extract_json(text):
    """Pull the payload out of a model's answer.

    Hermes writes prose around its JSON and usually fences it. Neither should cost
    the reader an edit, so try the whole thing, then each fenced block, then the
    outermost brace or bracket span. A bare list is normalised into the
    {"items": [...]} shape render.py expects.

    Fences must open and close at the start of a line: prose that merely mentions a
    fence mid-sentence is discussion, not data.
    """
    text = response_section(text)
    def shape(obj):
        if isinstance(obj, list):
            return {"items": obj}
        return obj if isinstance(obj, dict) else None

    candidates = [text]
    candidates += re.findall(r"^```(?:json)?[ \t]*$\n(.*?)^```[ \t]*$", text, re.S | re.M)
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = text.find(opener), text.rfind(closer)
        if i != -1 and j > i:
            candidates.append(text[i:j + 1])

    for blob in candidates:
        try:
            out = shape(json.loads(blob.strip()))
        except (json.JSONDecodeError, ValueError):
            continue
        if out is not None and isinstance(out.get("items"), list):
            return out
    return None


# ---------- judging what it said ----------

def host_of(url):
    """(displayable ascii host, whether it was non-ascii).

    Rendered in punycode deliberately. validate() strips bidi and control
    characters but cannot see that a Cyrillic 'сochrane.org' is not cochrane.org -
    the two are pixel-identical. Punycode makes the difference impossible to miss,
    which matters now that no whitelist stands behind the attribution.
    """
    host = (urlsplit(str(url or "")).hostname or "").strip()
    if not host:
        return "", False
    if host.isascii():
        return host, False
    try:
        return host.encode("idna").decode("ascii"), True
    except (UnicodeError, ValueError):
        return host, True


_GENERIC_LABELS = {"www", "com", "org", "net", "gov", "edu", "int", "co", "uk", "ac"}


def source_matches_host(item):
    """Whether the claimed source name plausibly relates to the host it links to.

    Deliberately a weak signal: it only suppresses the flag when the two obviously
    agree, so anything doubtful is surfaced for a human rather than judged by a
    rule. Being wrong in the noisy direction is the right way to be wrong here.
    """
    ascii_host, _ = host_of(item.get("url"))
    labels = [l for l in ascii_host.lower().split(".") if l not in _GENERIC_LABELS]
    claimed = re.sub(r"[^a-z0-9]", "", str(item.get("source", "")).lower())
    if not claimed:
        return False
    return any(claimed in l or l in claimed for l in labels if len(l) > 2)


def display_safe(item):
    """Sanitised copy of a rejected item, purely so it can be shown safely.

    Reuses render.clean() rather than adding a second sanitiser; the cap falls back
    to the title limit for fields LIMITS does not name, including url.
    """
    if not isinstance(item, dict):
        return {}
    return {k: (render.clean(v, render.LIMITS.get(k, 300)) if isinstance(v, str) else v)
            for k, v in item.items()}


def judge(payload, site):
    """Per-item verdicts, with validate() as the only authority on what may ship.

    validate() reports rejections to stderr for a whole batch, so to attribute a
    reason to one item it is re-run on that item alone. An item that passes alone
    but is missing from the batch result was a duplicate.
    """
    pillars = site["pillars"]
    raw = payload.get("items", []) if isinstance(payload, dict) else []

    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        survivors = render.validate(payload, pillars)
    kept_urls = {i["url"] for i in survivors}

    rows, seen = [], set()
    for n, item in enumerate(raw):
        one = io.StringIO()
        with contextlib.redirect_stderr(one):
            alone = render.validate({"items": [item]}, pillars)
        if alone:
            clean = alone[0]
            # validate() keeps the first occurrence of a url, so walking in the same
            # order means the first one here is the survivor and the rest are the
            # duplicates. Testing membership alone would mark every copy as kept.
            ok = clean["url"] in kept_urls and clean["url"] not in seen
            seen.add(clean["url"])
            reason = "" if ok else "duplicate url"
        else:
            clean, ok = None, False
            reason = one.getvalue().strip().split(":", 1)[-1].strip() or "rejected"

        # A rejected item never went through clean(), so its bidi overrides and
        # unbounded lengths would reach the review page unaltered - the one screen
        # where a human is deciding what to trust, and so the one place hostile text
        # most wants to be misread. Sanitise for display even though it cannot ship.
        shown = clean or display_safe(item)
        ascii_host, non_ascii = host_of(shown.get("url"))
        rows.append({
            "n": n,
            "ok": ok,
            "reason": reason,
            "item": clean,
            "title": str(shown.get("title") or "(no title)"),
            "why": str(shown.get("why") or ""),
            "brief": str(shown.get("brief") or ""),
            "pillar": str(shown.get("pillar") or ""),
            "score": shown.get("score", ""),
            "source": str(shown.get("source") or ""),
            "host": ascii_host,
            "non_ascii_host": non_ascii,
            "source_mismatch": bool(clean) and not source_matches_host(clean),
        })
    return rows, survivors


# ---------- git guards ----------

GIT_TIMEOUT = 120


def git(*args, raw=False):
    try:
        r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        # Usually a credential helper waiting on a dialog nobody can see from here.
        raise RuntimeError(
            f"git {args[0]} did not finish within {GIT_TIMEOUT}s - it is probably "
            "waiting on a credential prompt. Run `git push` once in a terminal, then retry.")
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {(r.stderr or r.stdout).strip()}")
    out = r.stdout or ""
    return out if raw else out.strip()


def index_is_clean(staged_names):
    """True when nothing except our one path is already staged.

    Pure, so it is testable without a repo. Unstaged edits elsewhere are not
    checked on purpose: `git add content/items.json` cannot pick them up, so only
    an already-dirty index could ride along in the commit.
    """
    return all(name == TRACKED for name in staged_names if name)


def staged_exactly_ours(staged_names):
    """True when the commit about to be made touches our path and nothing else."""
    return [n for n in staged_names if n] == [TRACKED]


def merge_items(new_items):
    """Today's findings on top of everything published before, newest first.

    This used to replace the file outright, which quietly destroyed a day's work on
    every publish - the archive page was archiving nothing. Items are keyed by url and
    the existing entry wins, so re-running a harvest cannot rewrite the record of
    something already published.
    """
    existing = []
    if ITEMS.exists():
        try:
            existing = json.loads(ITEMS.read_text(encoding="utf-8")).get("items", [])
        except (json.JSONDecodeError, OSError):
            existing = []

    seen = {i.get("url") for i in existing if isinstance(i, dict)}
    added = [i for i in new_items if i.get("url") not in seen]
    return added + existing, len(added)


def write_items(items):
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": items,
    }
    render.write(ITEMS, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def unpushed_count():
    """Commits sitting on main that never reached the remote."""
    try:
        return int(git("rev-list", "--count", "@{u}..HEAD") or 0)
    except (RuntimeError, ValueError):
        return 0                       # no upstream configured; nothing to reconcile


def commit_tracked_only(message):
    """Stage our one path, prove it is the only one staged, commit and push.

    Commit and push are reported separately on purpose. They used to be one step, so
    a push that failed after a successful commit left the content committed locally
    and absent from the site - and the next attempt said "nothing changed", which was
    exactly backwards. An unpushed commit is now finished rather than misreported.
    """
    git("add", "--", TRACKED)
    after = git("diff", "--cached", "--name-only").splitlines()

    if not [n for n in after if n]:
        if unpushed_count():
            git("push")
            return git("rev-parse", "--short", "HEAD") + " (pushed an earlier commit that had not reached the site)"
        raise RuntimeError("nothing changed - that is already what the site is publishing.")

    if not staged_exactly_ours(after):
        git("reset", "--", TRACKED)
        raise RuntimeError(f"refusing to commit: staged set was {after}, expected [{TRACKED}]")

    git("commit", "-m", message)
    sha = git("rev-parse", "--short", "HEAD")
    try:
        git("push")
    except RuntimeError as exc:
        raise RuntimeError(
            f"committed locally as {sha}, but the push failed and the site is unchanged: "
            f"{exc} Fix the connection and publish again - it will finish this commit.")
    return sha


def publish_items(items):
    before = git("diff", "--cached", "--name-only").splitlines()
    if not index_is_clean(before):
        raise RuntimeError(
            "something else is already staged: " + ", ".join(n for n in before if n)
            + ". Commit or unstage it first - an approval here must not carry other changes.")
    merged, added = merge_items(items)
    if not added:
        raise RuntimeError("every one of those is already published - nothing new to add.")
    write_items(merged)
    return commit_tracked_only(
        f"content: +{added} item(s) for {datetime.now(timezone.utc):%Y-%m-%d}"
        f"  ({len(merged)} total)")


def rollback():
    """Restore the previously published items.json.

    HEAD~1 is the wrong thing to reach for - the previous commit may not have
    touched content at all. Ask git which commits actually changed this path.
    """
    before = git("diff", "--cached", "--name-only").splitlines()
    if not index_is_clean(before):
        raise RuntimeError("something else is already staged; unstage it first.")
    hist = git("log", "--format=%H", "-n", "2", "--", TRACKED).splitlines()
    if len(hist) < 2:
        raise RuntimeError("no earlier version of the content to go back to.")
    previous = hist[1]
    render.write(ITEMS, git("show", f"{previous}:{TRACKED}", raw=True))
    commit_tracked_only(f"content: roll back to {previous[:7]}")
    return previous[:7]                 # the version now live, not the commit that did it


# ---------- the review page ----------

def build_preview(site, items):
    """The real index page, built by the same function the deploy uses."""
    tpl = render.TEMPLATE.read_text(encoding="utf-8")
    page, _ = render.build_index(site, tpl, sorted(items, key=render.sort_key, reverse=True))
    return page


def host_markup(host):
    """Host with its registrable tail emphasised, so cochrane.org.evil.com reads
    as evil.com rather than as cochrane.org."""
    parts = [p for p in host.split(".") if p]
    if len(parts) < 2:
        return html.escape(host)
    head = ".".join(parts[:-2])
    return (html.escape(head + ".") if head else "") + "<b>" + html.escape(".".join(parts[-2:])) + "</b>"


def row_markup(rows):
    out = []
    for r in rows:
        flags = []
        if r["non_ascii_host"]:
            flags.append('<span class="flag">non-ascii domain</span>')
        if r["source_mismatch"]:
            flags.append('<span class="flag">source does not match domain</span>')
        if not r["ok"]:
            flags.append(f'<span class="bad">{html.escape(r["reason"])}</span>')
        paras = [x.strip() for x in re.split(r"\n\s*\n", r["brief"]) if x.strip()]
        brief = ("".join(f'<p class="brief">{html.escape(x)}</p>' for x in paras)
                 if paras else "")
        out.append(f"""
  <li class="row {'ok' if r['ok'] else 'no'}">
    <label>
      <input type="checkbox" name="keep" value="{r['n']}" {'checked' if r['ok'] else 'disabled'}>
      <span class="t">{html.escape(r['title'])}</span>
    </label>
    <div class="meta">
      <span class="pillar">{html.escape(r['pillar'])}</span>
      {f'<span class="score">score {html.escape(str(r["score"]))}</span>' if str(r["score"]).strip() else ''}
      <span class="src">{html.escape(r['source'])}</span>
      <span class="host">{host_markup(r['host'])}</span>
      {' '.join(flags)}
    </div>
    <p class="why">{html.escape(r['why'])}</p>{brief}
  </li>""")
    return "\n".join(out)


class State:
    site = None
    rows = []
    digest = ""
    source_label = ""
    token = ""
    nonce = ""
    done = False
    result = ""
    last_seen = 0.0


def render_admin():
    tpl = ADMIN_TEMPLATE.read_text(encoding="utf-8")
    kept = sum(1 for r in State.rows if r["ok"])
    values = {
        "{{NONCE}}": State.nonce,
        "{{TOKEN}}": State.token,
        "{{SOURCE}}": html.escape(State.source_label),
        "{{COUNT}}": f"{kept} of {len(State.rows)} usable",
        "{{ROWS}}": row_markup(State.rows) or '<li class="row no">Nothing to review.</li>',
        "{{DIGEST}}": State.digest,
    }
    return re.sub(r"\{\{[A-Z_]+\}\}", lambda m: values.get(m.group(0), m.group(0)), tpl)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # -- guards ---------------------------------------------------------------

    def _host_ok(self):
        """Reject a Host header we did not expect - that is what DNS rebinding
        looks like from in here."""
        return (self.headers.get("Host") or "").split(":")[0] in ("127.0.0.1", "localhost")

    def _origin_ok(self):
        """A cross-site POST carries a foreign Origin. Same-origin sends ours or
        nothing at all."""
        return self.headers.get("Origin") in (
            None, "", f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}")

    def _token_ok(self, supplied):
        return secrets.compare_digest(str(supplied or ""), State.token)

    def _send(self, body, ctype="text/html; charset=utf-8", code=200, csp=None):
        raw = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Referrer-Policy", "no-referrer")
        if csp:
            self.send_header("Content-Security-Policy", csp)
        self.end_headers()
        self.wfile.write(raw)

    def _deny(self, why):
        self._send(why, "text/plain; charset=utf-8", code=403)

    # -- routes ---------------------------------------------------------------

    def do_GET(self):
        State.last_seen = time.time()
        if not self._host_ok():
            return self._deny("unexpected Host")
        url = urlsplit(self.path)
        q = parse_qs(url.query)
        if not self._token_ok((q.get("t") or [""])[0]):
            return self._deny("missing or bad session token")

        if url.path == "/":
            csp = ("default-src 'none'; "
                   f"script-src 'nonce-{State.nonce}'; style-src 'nonce-{State.nonce}'; "
                   "connect-src 'self'; frame-src 'self'; base-uri 'none'; form-action 'none'")
            return self._send(render_admin(), csp=csp)

        if url.path == "/preview":
            drop = {int(x) for x in (q.get("drop") or [""])[0].split(",") if x.strip().isdigit()}
            chosen = [r["item"] for r in State.rows if r["ok"] and r["n"] not in drop]
            return self._send(build_preview(State.site, chosen))

        self._deny("no such path")

    def do_POST(self):
        State.last_seen = time.time()
        if not self._host_ok():
            return self._deny("unexpected Host")
        if not self._origin_ok():
            return self._deny("cross-origin POST refused")

        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return self._deny("bad body")
        if not self._token_ok(body.get("token")):
            return self._deny("missing or bad session token")

        if not PUBLISH_LOCK.acquire(blocking=False):
            return self._send(json.dumps({"ok": False, "message": "already publishing."}),
                              "application/json", code=409)
        try:
            action = body.get("action")
            if action == "publish":
                # The bytes reviewed must be the bytes published. If a newer Hermes
                # run landed under this page, refuse rather than ship something that
                # was never on screen.
                if body.get("digest") != State.digest:
                    raise RuntimeError(
                        "the proposal changed since this page loaded. Reload and look again.")
                drop = {int(x) for x in body.get("drop", []) if str(x).isdigit()}
                chosen = [r["item"] for r in State.rows if r["ok"] and r["n"] not in drop]
                if not chosen:
                    raise RuntimeError("nothing selected.")
                sha = publish_items(sorted(chosen, key=render.sort_key, reverse=True))
                State.result = (f"Published {len(chosen)} item(s) as {sha}. "
                                f"The build takes about a minute: {State.site['url']}")
            elif action == "rollback":
                State.result = f"Rolled back to {rollback()}. The build is running."
            else:
                raise RuntimeError("unknown action")
            State.done = True
            self._send(json.dumps({"ok": True, "message": State.result}), "application/json")
        except Exception as exc:                    # surfaced in the page, never swallowed
            self._send(json.dumps({"ok": False, "message": str(exc)}),
                       "application/json", code=400)
        finally:
            PUBLISH_LOCK.release()


def serve():
    # Threading, because HTTP/1.1 keep-alive holds a connection open: a
    # single-connection loop would deadlock between the preview frame and the POST.
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    url = f"http://127.0.0.1:{PORT}/?t={State.token}"
    print(f"  review at {url}")
    print("  loopback only; exits once you publish, or after 30 minutes idle")
    State.last_seen = time.time()
    webbrowser.open(url)

    while not State.done and time.time() - State.last_seen < IDLE_SECONDS:
        time.sleep(0.25)

    httpd.shutdown()
    httpd.server_close()
    print("  " + (State.result or "closed without publishing."))


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="print the proposal and exit")
    ap.add_argument("--source", help="read this file instead of the newest Hermes run")
    ap.add_argument("--no-harvest", action="store_true",
                    help="never start a harvest; just show what is already there")
    args = ap.parse_args()

    site = render.load_json(render.SITE)

    if args.source:
        src, fresh = Path(args.source), True
    else:
        src = newest_output()
        fresh = bool(src) and harvest_age(src)[1]
        # A scheduled run only happens if the machine was awake for it. Rather than
        # report an empty hand, go and get one.
        if not fresh and not args.no_harvest:
            ok, why = run_harvest()
            if ok:
                src = newest_output()
                fresh = bool(src) and harvest_age(src)[1]
            elif src:
                print(f"  {why}" + chr(10) + "  showing the last harvest instead")
            else:
                sys.exit(f"no proposal available: {why}")

    if not src or not src.is_file():
        sys.exit("no proposal found, and no harvest could be run.")

    text = src.read_text(encoding="utf-8", errors="replace")
    payload = extract_json(text)
    if payload is None:
        sys.exit(f"could not find any JSON in {src.name} - Hermes may have answered in prose.")

    rows, _ = judge(payload, site)
    State.site = site
    State.rows = rows
    State.digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    State.source_label = (harvest_age(src)[0] if not args.source else src.name)
    State.token = secrets.token_urlsafe(32)
    State.nonce = secrets.token_urlsafe(16)

    usable = sum(1 for r in rows if r["ok"])
    when = harvest_age(src)[0] if not args.source else src.name

    if not usable:
        # Most things not making the cut is the entire editorial premise, so this is
        # a normal outcome and should not read like a crash.
        print(f"Nothing cleared the bar ({when}). "
              f"{'All ' + str(len(rows)) + ' candidate(s) were rejected' if rows else 'Hermes proposed nothing'}"
              " - so there is nothing to publish, and that is a legitimate day.")
        if rows:
            for r in rows:
                print(f"  [drop] {r['title'][:66]}   <- {r['reason']}")
        return

    print(f"{usable} of {len(rows)} item(s) usable  ({when})")
    if args.check:
        for r in rows:
            note = "" if r["ok"] else f"   <- {r['reason']}"
            print(f"  [{'keep' if r['ok'] else 'drop'}] {r['title'][:66]}"
                  f"  ({r['source']} / {r['host']}){note}")
        return

    serve()


if __name__ == "__main__":
    main()
