"""
Shared code for the publication tools (OpenAlex, ORCID, Crossref -> BibTeX).

Used by find-new-publications.py and apply-publication-decision.py.
Standard library only.
"""
import csv
import json
import os
import re
import ssl
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from difflib import SequenceMatcher

OPENALEX_ID = "A5023646639"
ORCID = "0000-0002-3530-7251"
MAILTO = os.environ.get("PUB_MAILTO", "your.email@canterbury.ac.nz")  # set in the workflow

HEADERS = {"User-Agent": f"marshall-echem-lab-pubtools/1.0 (mailto:{MAILTO})"}

# ======================================================================
# HTTP (with macOS keychain fallback for certificate problems)
# ======================================================================
_SSL_CTX = None


def mac_keychain_context():
    keychains = [
        "/System/Library/Keychains/SystemRootCertificates.keychain",
        "/Library/Keychains/System.keychain",
        os.path.expanduser("~/Library/Keychains/login.keychain-db"),
    ]
    keychains = [k for k in keychains if os.path.exists(k)]
    pem = subprocess.run(["security", "find-certificate", "-a", "-p", *keychains],
                         capture_output=True, text=True).stdout
    ctx = ssl.create_default_context()
    for block in pem.split("-----END CERTIFICATE-----"):
        if "-----BEGIN CERTIFICATE-----" in block:
            try:
                ctx.load_verify_locations(cadata=block + "-----END CERTIFICATE-----\n")
            except ssl.SSLError:
                pass
    return ctx


def get_json(url, params=None, headers=None):
    """GET a URL; return (status, parsed JSON or error text)."""
    global _SSL_CTX
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={**HEADERS, **(headers or {})})
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(req, timeout=60, context=_SSL_CTX) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:      # rate limited: wait and retry
                time.sleep(5)
                continue
            return e.code, e.read().decode("utf-8", "replace")
        except urllib.error.URLError as e:
            cert = isinstance(e.reason, ssl.SSLCertVerificationError)
            if cert and _SSL_CTX is None and sys.platform == "darwin":
                print("  (certificate check failed; using the macOS keychain)")
                _SSL_CTX = mac_keychain_context()
                continue
            if cert:
                sys.exit(f"SSL certificate error: {e.reason}")
            if attempt < 3:
                time.sleep(3)
                continue
            raise
    return 0, "failed"


# ======================================================================
# Text helpers
# ======================================================================
def norm_doi(d):
    if not d:
        return None
    d = d.lower().strip()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    return d.rstrip(".")


def norm_text(s):
    s = unicodedata.normalize("NFKD", s or "").lower()
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def similar(a, b):
    return SequenceMatcher(None, norm_text(a), norm_text(b)).ratio()


def ascii_slug(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def tex_title(t):
    """HTML/MathML-ish title -> BibTeX-safe title, protecting chemistry."""
    t = t or ""
    # Crossref often wraps <sub>/<sup> in line breaks: "H\n <sub>2</sub>\n O"
    t = re.sub(r"\s*\n\s*", " ", t)
    t = re.sub(r"\s+<(sub|sup)>", r"<\1>", t)                     # "H <sub>2" -> "H<sub>2"
    t = re.sub(r"</(sub|sup)>\s+(?![A-Za-z][a-z]{2,})", r"</\1>", t)  # "O<sub>2</sub> –SiO"
    t = re.sub(r"</(sub|sup)>(?=[A-Za-z][a-z]{2,})", r"</\1> ", t)    # "CO<sub>2</sub>Reduction"
    t = re.sub(r"<sub>\s*(.*?)\s*</sub>", r"$_{\1}$", t, flags=re.I | re.S)
    t = re.sub(r"<sup>\s*(.*?)\s*</sup>", r"$^{\1}$", t, flags=re.I | re.S)
    t = re.sub(r"<[^>]+>", "", t)                       # drop any other tags
    t = re.sub(r"\{sub\s*(.*?)\}", r"$_{\1}$", t)       # OSTI-style {sub x}
    t = " ".join(t.split())
    t = t.replace("&amp;", "&").replace("&", r"\&").replace("%", r"\%")
    return protect(t)


COMMON = {"A", "I", "An", "On", "In", "Of", "To", "By", "At", "As", "Is", "It",
          "Or", "No", "Up", "We", "Be", "Do", "Go", "So", "If"}
PROPER = {"Australasia", "Nafion", "Newton's", "Prussian", "Tafel", "Zealand",
          "Fischer", "Tropsch", "Canterbury"}


def protect(t):
    """Brace words whose capitals must survive sentence-casing (APA etc.):
    formulas (CO$_{2}$, MgO), acronyms (PEM), camel case (iR, NiFe),
    element symbols (Au, Li–S, N-doped) and a few proper nouns."""
    out = []
    for w in t.split(" "):
        core = w.strip("()[],.:;\"'?")
        parts = [p for p in re.split(r"[-‐–—/]", core) if p]

        def special(p):
            return (re.search(r"[A-Z].*[A-Z]", p) or re.search(r"[a-z][A-Z]", p)
                    or re.search(r"[A-Za-z]\d|\d[A-Za-z]", p)
                    or (re.fullmatch(r"[A-Z][a-z]?", p) and p not in COMMON))
        if core and (("$" in core and re.search(r"[A-Z]", core))
                     or core in PROPER or any(special(p) for p in parts)):
            w = w.replace(core, "{" + core + "}", 1)
        out.append(w)
    return " ".join(out)


def tex_field(s):
    return (s or "").replace("&amp;", "&").replace("&", r"\&").replace("%", r"\%")


# ======================================================================
# Sources
# ======================================================================
def fetch_openalex():
    url = "https://api.openalex.org/works"
    params = {
        "filter": f"author.id:{OPENALEX_ID}",
        "per_page": 200,
        "cursor": "*",
        "select": "id,doi,display_name,publication_year,type,primary_location,"
                  "authorships,biblio",
        "mailto": MAILTO,
    }
    if os.environ.get("OPENALEX_API_KEY"):
        params["api_key"] = os.environ["OPENALEX_API_KEY"]
    works = []
    while params["cursor"]:
        status, data = get_json(url, params)
        if status != 200:
            sys.exit(f"OpenAlex error: HTTP {status} {str(data)[:200]}")
        works += data["results"]
        params["cursor"] = data["meta"].get("next_cursor")
    items = []
    for w in works:
        loc = w.get("primary_location") or {}
        b = w.get("biblio") or {}
        authors = []
        for a in w.get("authorships") or []:
            name = ((a.get("author") or {}).get("display_name")
                    or a.get("raw_author_name") or "")
            if name:
                authors.append(name)
        pages = b.get("first_page") or ""
        if b.get("last_page") and b.get("last_page") != pages:
            pages = f"{pages}--{b['last_page']}"
        items.append({
            "source": "openalex",
            "openalex": w["id"].rsplit("/", 1)[-1],
            "doi": norm_doi(w.get("doi")),
            "title": w.get("display_name") or "",
            "year": w.get("publication_year"),
            "venue": ((loc.get("source") or {}).get("display_name")) or "",
            "type": w.get("type") or "",
            "authors": authors,
            "volume": b.get("volume") or "",
            "number": b.get("issue") or "",
            "pages": pages,
            "url": loc.get("landing_page_url") or "",
        })
    return items


def fetch_orcid():
    status, data = get_json(f"https://pub.orcid.org/v3.0/{ORCID}/works",
                            headers={"Accept": "application/json"})
    if status != 200:
        print(f"  ORCID error: HTTP {status}; continuing without ORCID")
        return []
    items = []
    for g in data.get("group", []):
        s = g["work-summary"][0]
        doi = None
        for e in (g.get("external-ids") or {}).get("external-id", []):
            if e.get("external-id-type") == "doi":
                doi = norm_doi(e.get("external-id-value"))
                break
        year = (((s.get("publication-date") or {}).get("year")) or {}).get("value")
        items.append({
            "source": "orcid",
            "openalex": "",
            "doi": doi,
            "title": ((s.get("title") or {}).get("title") or {}).get("value", ""),
            "year": int(year) if year and year.isdigit() else None,
            "venue": ((s.get("journal-title") or {}).get("value", "")
                      if s.get("journal-title") else ""),
            "type": s.get("type", ""),
            "authors": [],
            "volume": "", "number": "", "pages": "", "url": "",
        })
    return items


def fetch_crossref(doi):
    status, data = get_json(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}",
                            {"mailto": MAILTO})
    if status != 200:
        return None
    return data.get("message")


def date_year(obj):
    try:
        return int(obj["date-parts"][0][0])
    except (TypeError, KeyError, IndexError, ValueError):
        return None


def apply_crossref(item, cr):
    """Fill an item from Crossref, choosing the journal-issue year where known."""
    authors = []
    for a in cr.get("author") or []:
        if a.get("family"):
            authors.append(f"{a['family']}, {a.get('given', '')}".strip(", "))
        elif a.get("name"):
            authors.append(a["name"])
    if authors:
        item["authors"] = authors
    if cr.get("title"):
        item["title"] = cr["title"][0]
    if cr.get("container-title"):
        item["venue"] = cr["container-title"][0]
    item["volume"] = cr.get("volume") or item["volume"]
    item["number"] = cr.get("issue") or item["number"]
    pages = cr.get("page") or cr.get("article-number") or item["pages"]
    a, _, z = pages.replace("--", "-").partition("-")
    item["pages"] = a if (not z or z == a) else f"{a}--{z}"
    item["publisher"] = cr.get("publisher", "")
    item["cr_type"] = cr.get("type", "")
    for src, label in (((cr.get("journal-issue") or {}).get("published-print"), "issue"),
                       (cr.get("published-print"), "print"),
                       (cr.get("issued"), "issued")):
        y = date_year(src)
        if y:
            item["year"], item["year_source"] = y, label
            break
    item["year_online"] = date_year(cr.get("published-online"))


# ======================================================================
# Categorise
# ======================================================================
CONF_VENUE = re.compile(r"proceedings|conference|congress|chemeca|meeting|"
                        r"ecs transactions|matec|symposium|workshop", re.I)


def categorise(it):
    t, v, title = it["type"].lower(), it["venue"], it["title"]
    doi = it["doi"] or ""
    if re.search(r"repository", v, re.I):
        return "exclude:repository"
    if t == "erratum" or re.match(r"(correction|corrigendum|erratum)\b", title, re.I):
        return "exclude:erratum"
    if (t in ("preprint", "posted-content") or doi.startswith("10.2139/")
            or re.search(r"ssrn|arxiv|chemrxiv|research square", v, re.I)):
        return "preprint"
    if t in ("dissertation", "dissertation-thesis"):
        return "thesis"
    if t == "conference-abstract" or re.search(r"meeting abstracts", v, re.I):
        return "conference-abstract"
    if t in ("conference-paper", "proceedings-article", "conference-proceedings") \
            or CONF_VENUE.search(v):
        return "conference-paper"
    if t in ("book-chapter",):
        return "book-chapter"
    if t in ("article", "journal-article", "review", "editorial", "letter") and v:
        return "journal"
    return "other"


# ======================================================================
# Merge
# ======================================================================
def merge(oa, orc):
    """OpenAlex is the base; add ORCID items that OpenAlex lacks."""
    items = list(oa)
    by_doi = {i["doi"]: i for i in items if i["doi"]}
    for o in orc:
        if o["doi"] and o["doi"] in by_doi:
            by_doi[o["doi"]]["source"] = "both"
            continue
        if not o["doi"]:
            match = next((i for i in items
                          if similar(i["title"], o["title"]) > 0.92), None)
            if match:
                match["source"] = "both"
                continue
        items.append(o)
        if o["doi"]:
            by_doi[o["doi"]] = o
    return items


# ======================================================================
# BibTeX
# ======================================================================
STOP = {"a", "an", "the", "on", "of", "in", "for", "and", "to", "with", "by",
        "from", "using", "via", "is", "are", "why", "how", "what"}


def make_key(it, used):
    first = it["authors"][0] if it["authors"] else "anon"
    family = first.split(",")[0] if "," in first else first.split()[-1]
    word = next((w for w in norm_text(it["title"]).split() if w not in STOP), "x")
    base = f"{ascii_slug(family)}{it['year'] or 'nd'}{ascii_slug(word)}"
    key, n = base, 0
    while key in used:
        n += 1
        key = base + "abcdefghijklmnopqrstuvwxyz"[n - 1]
    used.add(key)
    return key


def bib_authors(names):
    out = []
    for n in names:
        if "," in n:
            out.append(n)
        else:  # "Given Family" -> "Family, Given"
            parts = n.split()
            out.append(f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) > 1 else n)
    return " and ".join(tex_field(a) for a in out)


def to_bibtex(it):
    cat = it["category"]
    title = it["title"]
    if cat == "thesis":
        title = re.sub(r"\s*:\s*a thesis presented.*$", "", title, flags=re.I)
        title = re.sub(r"\s+:", ":", title)
        etype = "mastersthesis" if re.search(r"master", it["title"], re.I) else "phdthesis"
    else:
        etype = {"journal": "article", "conference-abstract": "inproceedings",
                 "conference-paper": "inproceedings", "book-chapter": "incollection",
                 }.get(cat, "misc")
    f = [("author", bib_authors(it["authors"])), ("title", "{" + tex_title(title) + "}")]
    venue = tex_field(it["venue"])
    if etype == "article":
        f.append(("journal", venue))
    elif etype in ("inproceedings", "incollection"):
        f.append(("booktitle", venue))
    elif etype in ("phdthesis", "mastersthesis"):
        f.append(("school", "{" + venue + "}"))
    elif venue:
        f.append(("howpublished", venue))
    f += [("year", str(it["year"] or "")), ("volume", it["volume"]),
          ("number", it["number"]), ("pages", it["pages"])]
    if it["doi"]:
        f.append(("doi", it["doi"]))
    elif it["url"]:
        f.append(("url", it["url"]))
    kw = [cat] + (["review"] if it["type"] == "review" else [])
    f.append(("keywords", ",".join(kw)))
    if it["openalex"]:
        f.append(("openalex", it["openalex"]))
    body = ",\n".join(f"  {k:<9} = {{{v}}}" if k != "title" else f"  {k:<9} = {v}"
                      for k, v in f if v)
    return f"@{etype}{{{it['key']},\n{body}\n}}\n"


# ======================================================================
# Reading the existing bib and curation file
# ======================================================================
def read_bib(path):
    """Return a list of {key, doi, openalex, title, raw} from a .bib file."""
    text = open(path, encoding="utf-8").read()
    out = []
    for chunk in re.split(r"(?m)^(?=@)", text):
        m = re.match(r"@\w+\{([^,]+),", chunk)
        if not m:
            continue

        def field(name):
            f = re.search(rf"(?m)^\s*{name}\s*=\s*\{{(.*)\}},?\s*$", chunk)
            return f.group(1) if f else ""
        out.append({"key": m.group(1).strip(), "doi": norm_doi(field("doi")),
                    "openalex": field("openalex"),
                    "title": re.sub(r"[{}$_^]", "", field("title")),
                    "raw": chunk.strip()})
    return out


def read_curation(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            c = json.load(fh)
    else:
        c = {}
    c.setdefault("reject", [])
    c.setdefault("reject_titles", [])
    c.setdefault("override", {})
    return c


def ecs_meeting_year(it):
    """ECS Meeting Abstracts: the DOI encodes the meeting year (ma2005-01)."""
    m = re.match(r"10\.1149/ma(\d{4})-", it.get("doi") or "")
    if m:
        it["year"], it["year_source"] = int(m.group(1)), "meeting"
