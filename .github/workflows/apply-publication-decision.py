#!/usr/bin/env python3
"""
Apply your decision on a publication-candidate issue.

Run by .github/workflows/publication-decision.yml when an issue is labelled
`approved` or `rejected`. Reads the issue from the GitHub event payload.

approved -> append the issue's BibTeX block to data/publications.bib
rejected -> add the DOI/OpenAlex ID to the reject list in data/curation.json

Writes a one-line result to decision.txt, which the workflow posts as a comment.
Exits non-zero (and explains in decision.txt) if the entry can't be added.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pubtools as pt  # noqa: E402

BIB = "data/publications.bib"
CURATION = "data/curation.json"


def result(msg, ok=True):
    with open("decision.txt", "w", encoding="utf-8") as fh:
        fh.write(msg + "\n")
    print(msg)
    sys.exit(0 if ok else 1)


def main():
    event = json.load(open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8"))
    label = event["label"]["name"]
    body = event["issue"].get("body") or ""

    m = re.search(r"<!--\s*pub-ids:(.*?)-->", body, re.S)
    ids = {}
    if m:
        for part in m.group(1).split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                if v.strip():
                    ids[k.strip()] = v.strip()

    if label == "rejected":
        cur = pt.read_curation(CURATION)
        new = [v for v in ids.values() if v.lower() not in {r.lower() for r in cur["reject"]}]
        if not new:
            result("Nothing to record: no DOI or OpenAlex ID on this issue, or already rejected.")
        cur["reject"].extend(new)
        with open(CURATION, "w", encoding="utf-8") as fh:
            json.dump(cur, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        result(f"Rejected. Added {', '.join(new)} to the reject list; it won't be proposed again.")

    # approved
    blk = re.search(r"```bibtex\s*\n(.*?)\n```", body, re.S)
    if not blk:
        result("Couldn't find the ```bibtex block in the issue. Nothing added.", ok=False)
    entry = blk.group(1).strip()
    if entry.count("{") != entry.count("}"):
        result("The BibTeX block has unbalanced braces { }. Fix the issue text, "
               "then remove and re-add the `approved` label.", ok=False)
    km = re.match(r"@\w+\{([^,\s]+),", entry)
    if not km:
        result("The BibTeX block doesn't start with @type{key, ... Nothing added.", ok=False)

    bib = pt.read_bib(BIB)
    doi = pt.norm_doi(ids.get("doi"))
    if doi and any(b["doi"] == doi for b in bib):
        result(f"Already in the bib (DOI {doi}). Nothing added.")

    key = km.group(1)
    keys = {b["key"] for b in bib}
    if key in keys:  # someone else took the key since the issue was opened
        n = 0
        while f"{key}{'abcdefghijklmnopqrstuvwxyz'[n]}" in keys:
            n += 1
        new_key = f"{key}{'abcdefghijklmnopqrstuvwxyz'[n]}"
        entry = entry.replace(key, new_key, 1)
        key = new_key

    text = open(BIB, encoding="utf-8").read().rstrip("\n")
    with open(BIB, "w", encoding="utf-8") as fh:
        fh.write(text + "\n\n" + entry + "\n")
    result(f"Approved. Added `{key}` to data/publications.bib; the site will rebuild shortly.")


if __name__ == "__main__":
    main()
