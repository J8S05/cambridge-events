"""Add or delete termcard/email events from a GitHub issue.

The website's "Add event" button opens a pre-filled GitHub issue whose body
contains a ```json block like:
    {"add": [{"title": ..., "start": "2026-11-20T19:30", ...}], "delete": ["manual-ab12cd34ef"]}
The workflow runs this script with the issue text in ISSUE_BODY; it updates
termcards.json and rebuilds events.json / the calendar feeds.
"""
import json
import os
import re
import sys
import uuid

import scraper

FIELDS = ("title", "start", "end", "venue", "speaker", "organiser", "url", "description", "booking")
DATE_RX = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?$")


def main():
    body = os.environ.get("ISSUE_BODY", "")
    m = re.search(r"```json\s*(\{.*?\})\s*```", body, re.S)
    if not m:
        sys.exit("No ```json block found in the issue.")
    req = json.loads(m.group(1))
    items = json.loads(scraper.MANUAL_FILE.read_text()) if scraper.MANUAL_FILE.exists() else []
    added, removed = [], []

    for ev in req.get("add") or []:
        keep = {k: str(ev.get(k) or "").strip()[:3000] for k in FIELDS}
        if not keep["title"] or not DATE_RX.match(keep["start"]):
            print(f"Skipped (needs a title and a date): {keep['title'] or '(no title)'}")
            continue
        if keep["end"] and not DATE_RX.match(keep["end"]):
            keep["end"] = ""
        if ev.get("access") in scraper.ACCESS_LABELS:
            keep["access"] = ev["access"]
        if ev.get("topics"):
            keep["topics"] = [t for t in ev["topics"] if t in scraper.TOPICS]
        keep = {k: v for k, v in keep.items() if v}
        keep["id"] = "manual-" + uuid.uuid4().hex[:10]
        items.append(keep)
        added.append(f"{keep['start'].replace('T', ' ')}  {keep['title']}")

    gone = set(req.get("delete") or [])
    if gone:
        removed = [i["title"] for i in items if i.get("id") in gone]
        items = [i for i in items if i.get("id") not in gone]

    scraper.MANUAL_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=1))
    scraper.refresh_manual()

    lines = []
    if added:
        lines.append("Added:\n" + "\n".join(f"- {a}" for a in added))
    if removed:
        lines.append("Deleted:\n" + "\n".join(f"- {r}" for r in removed))
    summary = "\n\n".join(lines) or "Nothing was changed (no valid events found)."
    summary += "\n\nThe website and calendar feeds update in a minute or two."
    print(summary)
    with open(os.environ.get("SUMMARY_FILE", "add_summary.md"), "w") as f:
        f.write(summary)


if __name__ == "__main__":
    main()
