#!/usr/bin/env python3
"""Collect third-party licenses, compare them with a previous run and render a Markdown report.

Stdlib only, so it runs on any GitHub-hosted runner without extra installs.

  license_report.py --type maven   --input licenses.tsv     --out-dir out [--baseline old/licenses.tsv]
  license_report.py --type pnpm    --input licenses.json    --out-dir out [--baseline ...]
  license_report.py --type flutter --input <project dir>    --out-dir out [--baseline ...]

Writes out/licenses.tsv (name, version, license, category), out/report.md and,
if something relevant changed, out/changes.md. Prints `changed`, `unknown-count`
and `copyleft-count` to $GITHUB_OUTPUT when that variable is set.
"""
import argparse
import collections
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse, unquote

PERMISSIVE = "permissive"
WEAK_COPYLEFT = "weak-copyleft"
STRONG_COPYLEFT = "strong-copyleft"
UNKNOWN = "unknown"

# Order matters: the first matching pattern wins for a single license term.
PATTERNS = [
    (WEAK_COPYLEFT, r"\blgpl|lesser general public|library general public"),
    (STRONG_COPYLEFT, r"\bagpl|affero"),
    # GPL with classpath exception behaves like weak copyleft for linking.
    (WEAK_COPYLEFT, r"(gpl|general public).*(classpath|cpe)"),
    (STRONG_COPYLEFT, r"\bgpl|general public license"),
    (WEAK_COPYLEFT, r"\bmpl\b|mpl-|mozilla|\bepl\b|epl-|eclipse public|\bcddl|common development and distribution"),
    (PERMISSIVE, r"apache|\bmit\b|mit-|\bx11\b|\bbsd|0bsd|\bisc\b|\bedl\b|eclipse distribution|cc0|public domain|unlicense"
                 r"|blueoak|python-2|psf|zlib|bouncy castle|go license|wtfpl|cc-by-|similar to apache|\bw3c|json license"
                 r"|boost|bsl-1|artistic|ofl|unicode"),
]

RANK = {PERMISSIVE: 0, WEAK_COPYLEFT: 1, STRONG_COPYLEFT: 2, UNKNOWN: 3}


def classify_term(term):
    t = term.strip().lower()
    if not t or t in ("unknown", "none", "unlicensed", "see license", "custom"):
        return UNKNOWN
    for category, pattern in PATTERNS:
        if re.search(pattern, t):
            return category
    return UNKNOWN


def classify(license_expr):
    """Most permissive alternative wins (dual licensing); within an AND, the most restrictive part."""
    expr = license_expr.strip().strip("()")
    alternatives = re.split(r"\s*\|\s*|\s+OR\s+|\s*/\s*(?=[A-Za-z])", expr)
    best = UNKNOWN
    for alt in alternatives:
        parts = re.split(r"\s+AND\s+", alt.strip().strip("()"))
        worst = max((classify_term(p.strip("() ")) for p in parts), key=RANK.get)
        if RANK[worst] < RANK[best]:
            best = worst
    return best


# --- collectors -------------------------------------------------------------

def collect_maven(path):
    """TSV produced by license-maven-plugin with templates/maven-licenses.ftl."""
    rows = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        name, version, lic = (line.split("\t") + ["", ""])[:3]
        rows.append((name, version, lic or "Unknown"))
    return rows


def collect_pnpm(path):
    """Output of `pnpm licenses list --json`."""
    data = json.loads(Path(path).read_text() or "{}")
    rows = []
    for key, packages in data.items():
        for p in packages:
            rows.append((p["name"], ",".join(p.get("versions", [])), p.get("license") or key or "Unknown"))
    return rows


def sniff_license_text(text):
    t = text[:20000]
    if "GNU LESSER GENERAL PUBLIC" in t.upper():
        return "LGPL"
    if "GNU AFFERO" in t.upper():
        return "AGPL"
    if "GNU GENERAL PUBLIC" in t.upper():
        return "GPL"
    if "Mozilla Public License" in t:
        return "MPL-2.0"
    if "Apache License" in t:
        return "Apache-2.0"
    if "Redistributions of source code" in t:
        return "BSD-3-Clause" if "endorse or promote" in t else "BSD-2-Clause"
    if "Permission is hereby granted, free of charge" in t:
        return "MIT"
    if "Permission to use, copy, modify, and/or distribute" in t:
        return "ISC"
    if "This is free and unencumbered software" in t:
        return "Unlicense"
    return "Unknown"


def collect_flutter(project_dir):
    """Reads .dart_tool/package_config.json (after `flutter pub get`) and each package's LICENSE file."""
    project = Path(project_dir).resolve()
    config_path = project / ".dart_tool" / "package_config.json"
    config = json.loads(config_path.read_text())
    versions = {}
    lock = project / "pubspec.lock"
    if lock.exists():
        for m in re.finditer(r'\n  ([a-z0-9_]+):\n(?:    .*\n|      .*\n)*?    version: "([^"]+)"', lock.read_text()):
            versions[m.group(1)] = m.group(2)
    root_name = None
    pubspec = project / "pubspec.yaml"
    if pubspec.exists():
        m = re.search(r"^name:\s*(\S+)", pubspec.read_text(), re.M)
        root_name = m.group(1) if m else None
    rows = []
    for p in config["packages"]:
        if p["name"] == root_name:
            continue
        uri = p["rootUri"]
        if uri.startswith("file://"):
            root = Path(unquote(urlparse(uri).path))
        else:
            root = (config_path.parent / uri).resolve()
        files = []
        # SDK packages (flutter_test, ...) carry no own LICENSE; it sits in the SDK root a few levels up.
        for directory in [root, *list(root.parents)[:3]]:
            files = sorted(f for f in directory.glob("*") if re.match(r"(?i)(license|licence|copying)", f.name) and f.is_file())
            if files:
                break
        lic =sniff_license_text(files[0].read_text(errors="ignore")) if files else "Unknown"
        rows.append((p["name"], versions.get(p["name"], ""), lic))
    return rows


COLLECTORS = {"maven": collect_maven, "pnpm": collect_pnpm, "flutter": collect_flutter}


# --- report -----------------------------------------------------------------

def load_tsv(path):
    result = {}
    if path and Path(path).is_file():
        for line in Path(path).read_text().splitlines()[1:]:
            cols = line.split("\t")
            if len(cols) >= 4:
                result[cols[0]] = {"version": cols[1], "license": cols[2], "category": cols[3]}
    return result


def md_escape(s):
    return s.replace("|", "\\|")


def table(rows, header):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(md_escape(str(c)) for c in r) + " |" for r in rows]
    return "\n".join(out)


def run_url():
    server, repo, run_id = (os.environ.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{server}/{repo}/actions/runs/{run_id}" if server and repo and run_id else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", required=True, choices=COLLECTORS)
    ap.add_argument("--input", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--baseline")
    ap.add_argument("--title", default="License report")
    args = ap.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    current = {}
    for name, version, lic in COLLECTORS[args.type](args.input):
        lic = " ".join(lic.split())
        current[name] = {"version": version, "license": lic, "category": classify(lic)}

    with open(out / "licenses.tsv", "w") as f:
        f.write("name\tversion\tlicense\tcategory\n")
        for name in sorted(current):
            c = current[name]
            f.write(f"{name}\t{c['version']}\t{c['license']}\t{c['category']}\n")

    baseline = load_tsv(args.baseline)
    has_baseline = bool(baseline)

    added = sorted(n for n in current if n not in baseline) if has_baseline else []
    removed = sorted(n for n in baseline if n not in current) if has_baseline else []
    license_changed = sorted(n for n in current if n in baseline and baseline[n]["license"] != current[n]["license"])
    version_changed = sorted(n for n in current if n in baseline and baseline[n]["version"] != current[n]["version"])
    added_restricted = [n for n in added if current[n]["category"] != PERMISSIVE]
    relevant = bool(license_changed or added_restricted)

    by_category = collections.defaultdict(list)
    for name, c in current.items():
        by_category[c["category"]].append(name)
    by_license = collections.Counter(c["license"] for c in current.values())

    def pkg_rows(names, src=current):
        return [(n, src[n]["version"], src[n]["license"], src[n]["category"]) for n in sorted(names)]

    lines = [f"# {args.title}", ""]
    status = ("**Changes need review**" if relevant else "No relevant changes") if has_baseline else "First run, no baseline to compare against"
    lines += [f"{len(current)} dependencies · "
              f"{len(by_category[PERMISSIVE])} permissive · {len(by_category[WEAK_COPYLEFT])} weak copyleft · "
              f"{len(by_category[STRONG_COPYLEFT])} strong copyleft · {len(by_category[UNKNOWN])} unknown/not OSS", "",
              f"Status: {status}", ""]

    changes = []
    if license_changed:
        changes += ["## License changed", "",
                    table([(n, baseline[n]["license"], current[n]["license"], current[n]["category"]) for n in license_changed],
                          ["Package", "Before", "Now", "Category"]), ""]
    if added_restricted:
        changes += ["## New dependencies with copyleft or unrecognised licenses", "",
                    table(pkg_rows(added_restricted), ["Package", "Version", "License", "Category"]), ""]
    lines += changes

    if by_category[UNKNOWN]:
        lines += ["## Unknown or not open source", "", table(pkg_rows(by_category[UNKNOWN]), ["Package", "Version", "License", "Category"]), ""]
    copyleft = by_category[STRONG_COPYLEFT] + by_category[WEAK_COPYLEFT]
    if copyleft:
        lines += ["## Copyleft", "", "Usually acceptable for software you run yourself; check obligations before distributing.", "",
                  table(pkg_rows(copyleft), ["Package", "Version", "License", "Category"]), ""]
    if has_baseline:
        other = [n for n in added if n not in added_restricted]
        lines += ["## Other changes since last run", "",
                  f"- New permissive dependencies: {len(other)}" + (f" ({', '.join(other[:30])}{' …' if len(other) > 30 else ''})" if other else ""),
                  f"- Removed dependencies: {len(removed)}" + (f" ({', '.join(removed[:30])}{' …' if len(removed) > 30 else ''})" if removed else ""),
                  f"- Version updates: {len(version_changed)}", ""]
    lines += ["## Licenses", "", table(sorted(by_license.items(), key=lambda x: (-x[1], x[0])), ["License", "Packages"]), "",
              "<details><summary>All dependencies</summary>", "", table(pkg_rows(current), ["Package", "Version", "License", "Category"]),
              "", "</details>", ""]
    (out / "report.md").write_text("\n".join(lines))

    if relevant:
        url = run_url()
        body = [f"The weekly license check for **{args.title}** found changes that need a look.", ""]
        if url:
            body += [f"Full report: {url}", ""]
        (out / "changes.md").write_text("\n".join(body + changes))

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as f:
            f.write(f"changed={'true' if relevant else 'false'}\n")
            f.write(f"unknown-count={len(by_category[UNKNOWN])}\n")
            f.write(f"copyleft-count={len(copyleft)}\n")
    print(f"{len(current)} dependencies, relevant changes: {relevant}, unknown: {len(by_category[UNKNOWN])}", file=sys.stderr)


if __name__ == "__main__":
    main()
