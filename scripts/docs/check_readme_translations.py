#!/usr/bin/env python3
"""Check README language navigation and translation structure without network access."""

import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "docs/readme-languages.json"
FENCE = re.compile(r"(?m)^([`~]{3,})([^\n]*)\n([\s\S]*?)^\1\s*$")
LINK = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")
HEADING = re.compile(r"(?m)^## (.+)$")


def fenced_blocks(content):
    return [(match.group(2).strip(), match.group(3)) for match in FENCE.finditer(content)]


def links(content):
    return [match.group(1) for match in LINK.finditer(content)]


def main():
    languages = json.loads(INVENTORY.read_text(encoding="utf-8"))
    assert languages[0]["file"] == "README.md"
    codes = [entry["code"] for entry in languages]
    files = [entry["file"] for entry in languages]
    assert len(codes) == len(set(codes)) and len(files) == len(set(files))
    assert all(re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", code) for code in codes)
    assert all(re.fullmatch(r"README(?:\.[a-z]{2})?\.md", name) for name in files)
    source = (ROOT / "README.md").read_text(encoding="utf-8")
    source_fences = fenced_blocks(source)
    source_links = [unquote(urlsplit(url).path) for url in links(source)]
    source_headings = HEADING.findall(source)
    assert source_fences, "source README has no fenced examples"
    for entry in languages:
        path = ROOT / entry["file"]
        content = path.read_text(encoding="utf-8")
        assert content.count("## ") == len(source_headings), path
        assert fenced_blocks(content) == source_fences, f"changed code example: {path}"
        for language in languages:
            assert f"]({language['file']})" in content, f"missing language link: {path}"
        targets = [unquote(urlsplit(url).path) for url in links(content)]
        assert sorted(targets) == sorted(source_links), f"changed link targets: {path}"
        for url in links(content):
            parsed = urlsplit(url)
            if parsed.scheme or parsed.netloc or not parsed.path or parsed.path.startswith("#"):
                continue
            assert (ROOT / unquote(parsed.path)).exists(), f"missing local target: {path}: {url}"
        for fact in ("LimeSurvey 7.1.2", "19/19", "10/10", "Node.js 22", "Java 21", "GPL-2.0-or-later"):
            assert fact in content, f"missing product fact: {path}: {fact}"
    print(f"README translation checks passed for {len(languages)} languages")


if __name__ == "__main__":
    main()
