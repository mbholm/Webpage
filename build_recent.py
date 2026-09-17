"""
Regenerate the three "Recent ..." lists in index.html from research.html and op-eds.html.

Run:  python build_recent.py
It is also run automatically by the GitHub Action on every push, so the live site is always current.
site.js applies the same rules in the browser; this script just bakes the result into the HTML
so it is also correct when index.html is opened straight from disk or with JavaScript off.
"""
import re
import html
from pathlib import Path

ROOT = Path(__file__).parent
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}


def text(s: str) -> str:
    """Strip tags and collapse whitespace, keep entities as written."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip()


def date_key(s: str) -> int:
    m = re.search(r"([A-Za-z]+)\s+(\d{4})", s)
    if m and m.group(1).lower() in MONTHS:
        return int(m.group(2)) * 100 + MONTHS[m.group(1).lower()]
    y = re.search(r"(\d{4})", s)
    return int(y.group(1)) * 100 + 12 if y else 0


def month_year(key: int) -> str:
    if not key:
        return ""
    y, mo = divmod(key, 100)
    name = next((k for k, v in MONTHS.items() if v == mo), "")
    return f"{name.capitalize()} {y}" if name else str(y)


def papers_by_section(research_html: str):
    """Yield (section_id, article_html) for every .paper article in research.html."""
    main = re.search(r"<main[^>]*>(.*?)</main>", research_html, re.S).group(1)
    section = None
    for m in re.finditer(r'<h2 id="([^"]+)">|<article class="paper">(.*?)</article>', main, re.S):
        if m.group(1):
            section = m.group(1)
        else:
            yield section, m.group(2)


def parse_paper(section: str, a: str):
    title_div = re.search(r'<div class="title">(.*?)</div>', a, re.S).group(1)
    link = re.search(r'<a href="([^"]+)">(.*?)</a>', title_div, re.S)
    if link:
        href, title = link.group(1), text(link.group(2))
    else:
        href, title = f"research.html#{section}", text(re.sub(r'<span class="note">.*?</span>', "", title_div))
    authors = re.search(r'<div class="authors">(.*?)</div>', a, re.S)
    meta = re.search(r'<div class="meta">(.*?)</div>', a, re.S)
    venue = re.search(r'<span class="venue">(.*?)</span>', a, re.S)
    meta_text = text(meta.group(1)) if meta else ""
    m = re.search(r"This version:\s*([A-Za-z]+\s+\d{4})", meta_text) or \
        re.search(r"First version:\s*([A-Za-z]+\s+\d{4})", meta_text)
    key = date_key(m.group(1) if m else meta_text)
    return dict(href=href, title=title, key=key,
                authors=text(authors.group(1)) if authors else "",
                venue=text(venue.group(1)) if venue else "")


def li(href: str, title: str, side: str) -> str:
    return (f'    <li><span class="t"><a href="{html.escape(href, quote=True)}">{title}</a></span>'
            f'<span class="s">{side}</span></li>')


def build():
    research = (ROOT / "research.html").read_text(encoding="utf-8")
    opeds = (ROOT / "op-eds.html").read_text(encoding="utf-8")
    index_path = ROOT / "index.html"
    index = index_path.read_text(encoding="utf-8")

    papers = [parse_paper(s, a) for s, a in papers_by_section(research)]

    def replace_list(kind: str, items: list[str]):
        nonlocal index
        pat = re.compile(rf'(<ul class="recent" data-recent="{kind}"[^>]*>)(.*?)(</ul>)', re.S)
        if not pat.search(index):
            raise SystemExit(f'index.html: no <ul data-recent="{kind}"> found')
        index = pat.sub(lambda m: m.group(1) + "\n" + "\n".join(items) + "\n  " + m.group(3), index, count=1)

    for kind, default_sections in (("papers", "working-papers,rr"), ("publications", "published")):
        ul = re.search(rf'<ul class="recent" data-recent="{kind}"[^>]*>', index).group(0)
        limit = int((re.search(r'data-limit="(\d+)"', ul) or [None, "4"])[1])
        sections = (re.search(r'data-sections="([^"]+)"', ul) or [None, default_sections])[1].split(",")
        chosen = [p for s, p in zip((s for s, _ in papers_by_section(research)), papers) if s in sections]
        chosen.sort(key=lambda p: -p["key"])  # stable: document order breaks ties
        items = []
        for p in chosen[:limit]:
            if kind == "papers":
                side = (p["authors"] + " · " if p["authors"] else "") + month_year(p["key"])
            else:
                side = (p["venue"] + " · " if p["venue"] else "") + str(p["key"] // 100)
            items.append(li(p["href"], p["title"], side))
        replace_list(kind, items)

    ul = re.search(r'<ul class="recent" data-recent="opeds"[^>]*>', index).group(0)
    limit = int((re.search(r'data-limit="(\d+)"', ul) or [None, "3"])[1])
    entries = []
    for o in re.findall(r'<article class="oped">(.*?)</article>', opeds, re.S):
        a = re.search(r'<div class="t"><a href="([^"]+)">(.*?)</a>', o, re.S)
        date = re.search(r'<div class="date">(.*?)</div>', o, re.S)
        outlet = re.search(r'<div class="o">(.*?)</div>', o, re.S)
        date_text = text(date.group(1)) if date else ""
        side = (text(outlet.group(1)) + " · " if outlet else "") + date_text
        entries.append((date_key(date_text), li(a.group(1), text(a.group(2)), side)))
    entries.sort(key=lambda e: -e[0])  # newest first; stable, so page order breaks ties
    replace_list("opeds", [e[1] for e in entries[:limit]])

    index_path.write_text(index, encoding="utf-8", newline="\n")
    print("index.html updated")


if __name__ == "__main__":
    build()
