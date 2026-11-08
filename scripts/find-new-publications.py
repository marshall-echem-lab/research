#!/usr/bin/env python3
"""
Find publications on OpenAlex/ORCID that aren't yet in data/publications.bib,
and open one GitHub Issue per candidate with a proposed BibTeX entry.

Run from the repo root (by .github/workflows/find-publications.yml):
    python3 scripts/find-new-publications.py issues.json
    python3 scripts/find-new-publications.py issues.json --dry-run   # print only

issues.json is the output of
    gh issue list --label publication-candidate --state all --json body --limit 1000
so that anything already proposed (open, approved or rejected) isn't proposed again.
"""
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pubtools as pt  # noqa: E402

BIB = "data/publications.bib"
CURATION = "data/curation.json"
MAX_ISSUES = 15          # safety valve: never open more than this per run
LABEL = "publication-candidate"


def ids_from_issues(path):
    """DOIs/OpenAlex IDs already proposed, read from the hidden marker in each issue."""
    seen = set()
    if not path or not os.path.exists(path):
        return seen
    for issue in json.load(open(path, encoding="utf-8")):
        m = re.search(r"<!--\s*pub-ids:(.*?)-->", issue.get("body") or "", re.S)
        if m:
            for part in m.group(1).split(";"):
                if "=" in part:
                    v = part.split("=", 1)[1].strip().lower()
                    if v:
                        seen.add(v)
    return seen


def issue_body(it, bibtex):
    authors = it["authors"]
    who = "; ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
    lines = [
        f"**{it['title']}**",
        "",
        f"{who}  ",
        f"*{it['venue'] or 'no venue'}*, {it['year'] or 'no year'}  ",
        f"Category: `{it['category']}`  ",
        f"Found via: {it['source']}",
    ]
    if it["doi"]:
        lines.append(f"DOI: https://doi.org/{it['doi']}  ")
    if it["openalex"]:
        lines.append(f"OpenAlex: https://openalex.org/{it['openalex']}")
    lines += [
        "",
        "### Proposed BibTeX",
        "",
        "Edit this block if anything needs fixing (title, category in `keywords`, ...), "
        "then add the label **approved** or **rejected**.",
        "",
        "```bibtex",
        bibtex.strip(),
        "```",
        "",
        f"<!-- pub-ids: doi={it['doi'] or ''}; openalex={it['openalex'] or ''} -->",
    ]
    return "\n".join(lines)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    bib = pt.read_bib(BIB)
    cur = pt.read_curation(CURATION)

    known = {b["doi"] for b in bib if b["doi"]} | {b["openalex"].lower() for b in bib if b["openalex"]}
    known |= {r.lower() for r in cur["reject"]}
    known |= ids_from_issues(args[0] if args else None)
    known_titles = [b["title"] for b in bib]
    reject_titles = {pt.norm_text(t) for t in cur["reject_titles"]}
    override = {k.lower(): v for k, v in cur["override"].items()}
    used_keys = {b["key"] for b in bib}

    print("Fetching OpenAlex and ORCID ...")
    items = pt.merge(pt.fetch_openalex(), pt.fetch_orcid())
    print(f"  {len(items)} works found")

    candidates = []
    for it in items:
        ids = {x.lower() for x in (it["openalex"], it["doi"]) if x}
        if ids & known or pt.norm_text(it["title"]) in reject_titles:
            continue
        for i in ids:
            for k, v in override.get(i, {}).items():
                it[k] = v
        it["doi"] = pt.norm_doi(it["doi"])
        if it["doi"] and it["doi"] in known:
            continue
        # same title as something already in the bib (e.g. a second record of it)
        if any(pt.similar(it["title"], t) > 0.92 for t in known_titles):
            continue
        if it["doi"]:
            cr = pt.fetch_crossref(it["doi"])
            if cr:
                pt.apply_crossref(it, cr)
            time.sleep(0.1)
        pt.ecs_meeting_year(it)
        it["category"] = it.get("category") or pt.categorise(it)
        if it["category"].startswith("exclude"):
            print(f"  skip ({it['category']}): {it['title'][:70]}")
            continue
        if it["category"] == "preprint" and any(
                pt.similar(it["title"], t) > 0.85 for t in known_titles):
            print(f"  skip (superseded preprint): {it['title'][:70]}")
            continue
        candidates.append(it)

    print(f"{len(candidates)} new candidate(s)")
    if len(candidates) > MAX_ISSUES:
        print(f"  more than {MAX_ISSUES}: opening the first {MAX_ISSUES}; "
              "the rest will follow on the next run")
        candidates = candidates[:MAX_ISSUES]

    for it in candidates:
        it["key"] = pt.make_key(it, used_keys)
        body = issue_body(it, pt.to_bibtex(it))
        title = f"New publication: {it['title'][:90]}"
        if dry:
            print("\n" + "=" * 70 + f"\n{title}\n\n{body}")
            continue
        subprocess.run(["gh", "issue", "create", "--title", title, "--body", body,
                        "--label", LABEL], check=True)
        print(f"  opened issue: {title}")


if __name__ == "__main__":
    main()
