"""Fetch Cambridge Eventbrite events on this Mac and upload them to GitHub.

Eventbrite doesn't answer GitHub's servers, but it does answer a home connection.
This runs in the background every few hours (see install_mac_sync.sh). It keeps
its own copy of the repository, so it never touches the folder you use with
GitHub Desktop, and only ever changes eventbrite.json.
"""
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_URL = "https://github.com/J8S05/cambridge-events.git"
WORK = Path.home() / "Library" / "Application Support" / "CambridgeEvents" / "repo"


def git(*args, check=True):
    r = subprocess.run(["git", "-C", str(WORK), *args], capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def log(msg):
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M} {msg}", flush=True)


def main():
    os.environ["GIT_TERMINAL_PROMPT"] = "0"   # never wait for a password in the background
    if not (WORK / ".git").exists():
        WORK.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", REPO_URL, str(WORK)], check=True)
    git("fetch", "-q", "origin", "main")
    git("reset", "-q", "--hard", "origin/main")

    sys.path.insert(0, str(WORK))
    import scraper  # the latest version from GitHub

    events = scraper.scrape_eventbrite(log)
    if not events:
        log(f"Eventbrite returned no events ({'; '.join(dict.fromkeys(scraper.EB_DIAG)) or 'no matches'}); nothing uploaded.")
        return
    old = json.loads((WORK / "eventbrite.json").read_text()) if (WORK / "eventbrite.json").exists() else {}
    if old.get("events") == events and old.get("fetched", "") > (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat():
        log(f"{len(events)} events, unchanged; nothing uploaded.")
        return
    (WORK / "eventbrite.json").write_text(json.dumps(
        {"fetched": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "events": events},
        ensure_ascii=False, indent=1))
    git("add", "eventbrite.json")
    git("-c", "user.name=Eventbrite sync (Mac)", "-c", "user.email=J8S05@users.noreply.github.com",
        "commit", "-q", "-m", f"Eventbrite events from the Mac ({len(events)})")
    for attempt in range(3):
        r = subprocess.run(["git", "-C", str(WORK), "push", "-q", "origin", "HEAD:main"], capture_output=True, text=True)
        if r.returncode == 0:
            log(f"Uploaded {len(events)} Eventbrite events.")
            return
        git("pull", "-q", "--rebase", "origin", "main", check=False)   # GitHub updated meanwhile: retry on top
    raise RuntimeError(f"push failed: {r.stderr.strip()[:300]}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"ERROR: {e}")
        sys.exit(1)
