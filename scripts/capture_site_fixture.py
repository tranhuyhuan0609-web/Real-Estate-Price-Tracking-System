"""Turn a saved list page + detail page of an HTML site into small, anonymised test fixtures.

    uv run python scripts/capture_site_fixture.py mogi list.html detail.html

writes tests/fixtures/html/<site>_list.html and <site>_detail.html. Then update the expected
values in tests/unit/parsers/test_html_site_golden.py.

* list page: only the first ``--cards`` listing cards are kept, inside a copy of their parent
  element, so the ``items_path`` selector still matches;
* both pages: scripts, styles, SVG, iframes, header/footer/nav and comments are dropped;
* PII: phone numbers, e-mails (including Cloudflare ``data-cfemail``) and the advertiser name
  nodes selected by the site's ``detail_page.fields.advertiser_name`` are replaced with fakes.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import lxml.html
from lxml import etree
from parsel import Selector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from realestate.infrastructure.config.site_configs import load_site_config  # noqa: E402

OUT_DIR = ROOT / "tests" / "fixtures" / "html"
FAKE_EMAIL = "seller@example.com"
FAKE_NAME = "Nguyễn Văn A"
DROP_TAGS = (
    "script",
    "style",
    "noscript",
    "svg",
    "iframe",
    "header",
    "footer",
    "nav",
    "link",
    "meta",
)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?84|0)(?:[\s.\-]?\d){8,10}(?!\d)")
MASKED_PHONE_RE = re.compile(r"(?<!\d)(?:\+?84|0)\d[\d\s.]{1,10}[*xX]{2,}[\d*xX]*")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _fake_phone(match: re.Match[str], counter: list[int]) -> str:
    counter[0] += 1
    return f"09000000{counter[0]:02d}"


def _cf_encode(email: str, key: int = 0x42) -> str:
    return f"{key:02x}" + "".join(f"{ord(c) ^ key:02x}" for c in email)


def _scrub_text(text: str, counter: list[int]) -> str:
    text = EMAIL_RE.sub(FAKE_EMAIL, text)
    text = MASKED_PHONE_RE.sub("0900 000***", text)
    return PHONE_RE.sub(lambda m: _fake_phone(m, counter), text)


def _clean(root: etree._Element, counter: list[int]) -> None:
    etree.strip_elements(root, *DROP_TAGS, with_tail=False)
    for comment in root.xpath("//comment()"):
        comment.getparent().remove(comment)
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if el.text:
            el.text = _scrub_text(el.text, counter)
        if el.tail:
            el.tail = _scrub_text(el.tail, counter)
        for attr, value in list(el.attrib.items()):
            if attr == "data-cfemail":
                el.attrib[attr] = _cf_encode(FAKE_EMAIL)
            elif attr == "style":
                del el.attrib[attr]
            else:
                el.attrib[attr] = _scrub_text(value, counter)


def _name_nodes(root: etree._Element, css_list: str) -> list[etree._Element]:
    found: list[etree._Element] = []
    for css in (c.strip() for c in css_list.split(",")):
        css = re.sub(r"::(text|attr\(([^)]+)\))$", "", css)
        try:
            found.extend(root.cssselect(css))
        except Exception:
            continue
    return found


def collect_names(html: str, css_list: str | None) -> set[str]:
    if not css_list:
        return set()
    root = lxml.html.fromstring(html)
    names = set()
    for node in _name_nodes(root, css_list):
        for text in [node.text_content(), node.attrib.get("title", "")]:
            text = " ".join(text.split())
            if len(text) >= 3:
                names.add(text)
    return names


def _scrub_names(root: etree._Element, names: set[str]) -> None:
    """Replace full names anywhere, and given names after an honorific ("anh Diệp")."""
    if not names:
        return
    full = re.compile("|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)), re.I)
    given = {n.split()[-1] for n in names if len(n.split()[-1]) >= 2}
    honorific = re.compile(
        r"\b(anh|chị|cô|chú|mr\.?|ms\.?|liên hệ)(\s+)("
        + "|".join(re.escape(g) for g in given)
        + r")\b",
        re.I,
    )

    def fix(text: str) -> str:
        return honorific.sub(lambda m: f"{m.group(1)}{m.group(2)}A", full.sub(FAKE_NAME, text))

    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if el.text:
            el.text = fix(el.text)
        if el.tail:
            el.tail = fix(el.tail)
        for attr, value in list(el.attrib.items()):
            el.attrib[attr] = fix(value)


def _blank_names(root: etree._Element, css_list: str) -> None:
    """Replace the text of every node an advertiser_name selector can hit."""
    for css in (c.strip() for c in css_list.split(",")):
        css = re.sub(r"::(text|attr\(([^)]+)\))$", "", css)
        try:
            nodes = root.cssselect(css)
        except Exception:
            continue
        for node in nodes:
            node.text = FAKE_NAME
            for child in node:
                child.text = FAKE_NAME if child.text and child.text.strip() else child.text
            if "title" in node.attrib:
                node.attrib["title"] = FAKE_NAME


def build_list(
    html: str,
    items_path: str,
    cards: int,
    counter: list[int],
    name_css: str | None,
    names: set[str],
) -> str:
    doc = lxml.html.fromstring(html)
    sel = Selector(text=html)
    found = sel.xpath(items_path) if items_path.startswith(("/", "./")) else sel.css(items_path)
    if not found:
        raise SystemExit(f"items_path {items_path!r} matched nothing in the list page")
    first = doc.xpath(found[0].root.getroottree().getpath(found[0].root))[0]
    parent = first.getparent()
    kept = [c for c in parent if c.tag == first.tag][:cards]
    shell = lxml.html.Element(parent.tag, dict(parent.attrib))
    for card in kept:
        shell.append(card)
    _clean(shell, counter)
    if name_css:
        _blank_names(shell, name_css)
    _scrub_names(shell, names)
    body = lxml.html.tostring(shell, encoding="unicode", pretty_print=True)
    return f"<!DOCTYPE html>\n<html><body>\n{body}</body></html>\n"


def build_detail(html: str, name_css: str | None, counter: list[int], names: set[str]) -> str:
    doc = lxml.html.fromstring(html)
    body = doc.find("body") if doc.find("body") is not None else doc
    _clean(body, counter)
    if name_css:
        _blank_names(body, name_css)
    _scrub_names(body, names)
    return "<!DOCTYPE html>\n<html>" + lxml.html.tostring(body, encoding="unicode") + "</html>\n"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("site_id")
    ap.add_argument("list_html", type=Path)
    ap.add_argument("detail_html", type=Path)
    ap.add_argument("--cards", type=int, default=3)
    args = ap.parse_args()

    config = load_site_config(ROOT / "configs" / "sites" / f"{args.site_id}.yaml")
    counter = [0]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    name_css = config.detail_page.fields.get(
        "advertiser_name"
    ) or config.response_mapping.fields.get("advertiser_name")
    list_html = args.list_html.read_text(encoding="utf-8")
    detail_html = args.detail_html.read_text(encoding="utf-8")
    names = collect_names(list_html, name_css) | collect_names(detail_html, name_css)
    list_out = build_list(
        list_html,
        config.response_mapping.items_path,
        args.cards,
        counter,
        name_css,
        names,
    )
    detail_out = build_detail(detail_html, name_css, counter, names)
    for suffix, text in (("list", list_out), ("detail", detail_out)):
        path = OUT_DIR / f"{args.site_id}_{suffix}.html"
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(ROOT)} ({len(text) // 1024} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
