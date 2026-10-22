#!/usr/bin/env python3
"""Write the review digest for one immutable candidate commit.

  python digest.py --repo DIR --base SHA --candidate SHA [--out FILE] [--max-bytes N]

The digest holds three things a reviewer otherwise gathers with tool calls:

1. The diff of every changed file from the base to the candidate.
2. The full text of each changed file at the candidate, with line numbers.
3. The references, at the candidate and outside the changed files, to every symbol the
   diff declares. A symbol is a name declared on an added or a deleted line: a function,
   class, method, constant, variable, type, interface or enum, an exported name, or an
   object or property key. Names from both sides count. A name declared on added lines
   only is added, on deleted lines only is deleted, and on both is changed, which covers
   a declaration whose line changed. Language keywords and names shorter than 3
   characters are skipped. Each symbol lists at most 15 reference lines, then a count of
   the rest. Review criteria 8 and 10 ask for exactly these searches.

Every input comes from the Git object database, so the script needs no checkout and never
touches a working tree.

The format is fixed. A reviewer prompt, or a person writing a digest by hand, can rely on
these sections in this order:

  # Review digest
  ## Changed files
  ## Diff
  ## Symbol references
  ## Files at the candidate
  ## Not included

The whole digest, the "Not included" list with it, stays within --max-bytes. Items fill
the budget in this order: the diff of every file, then the symbol references, then the
full files, smallest first. An item that does not fit is left out and named under "Not
included" with its size. A skipped item never stops a smaller later item from fitting.

Generated lock files and binary files get one line under "Changed files" and nothing
else. A deleted file has a diff but no full text.

The last line on stderr is a JSON object with the digest's size, counts and the labels of
the items it left out.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

DEFAULT_MAX_BYTES = 300_000
MAX_HITS_PER_SYMBOL = 15
MIN_SYMBOL_LENGTH = 3
SECTIONS = ["Changed files", "Diff", "Symbol references", "Files at the candidate", "Not included"]

GENERATED_NAMES = {
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
    "Cargo.lock", "poetry.lock", "uv.lock", "Gemfile.lock", "composer.lock", "go.sum",
}
PROSE_SUFFIXES = (".md", ".markdown", ".txt", ".rst")
STYLE_SUFFIXES = (".css", ".scss", ".less")

NAME = r"[A-Za-z_$][\w$]*"
MODIFIERS = r"(?:(?:export|default|public|private|protected|static|async|readonly|override|abstract|declare|get|set)\s+)*"
DECLARATIONS = [
    # function, class, constant, variable, type, interface and enum keywords
    re.compile(rf"\b(?:function\*?|class|interface|enum|type|namespace|struct|trait|def|fn|const|let|var)\s+({NAME})"),
    re.compile(rf"\bfunc\s+(?:\([^)]*\)\s*)?({NAME})"),
    # a method or function head: name(args) {, with or without modifiers and a return type
    re.compile(rf"^\s*{MODIFIERS}\*?({NAME})\s*(?:<[^>]*>)?\s*\(.*\)\s*(?::[^={{}}]+)?\{{\s*$"),
    # a class field with a modifier
    re.compile(rf"^\s*(?:(?:public|private|protected|static|readonly|declare|override)\s+)+({NAME})"),
    # an object, interface, JSON or YAML key
    re.compile(rf"^\s*[\"']?({NAME})[\"']?\s*\??\s*:(?!:)"),
    # an assignment that declares a name, as Python and class fields do
    re.compile(rf"^\s*({NAME})\s*(?::\s*[^=]+)?=(?![=>])"),
    re.compile(rf"\b(?:this|self)\.({NAME})\s*=(?!=)"),
]
DESTRUCTURED = re.compile(r"\b(?:const|let|var)\s*[{\[]([^}\]=]*)[}\]]")
EXPORT_LIST = re.compile(r"\bexport\s*(?:type\s*)?\{([^}]*)\}")
STYLE_NAMES = [re.compile(r"(--[\w-]+)\s*:"), re.compile(r"[.#](-?[A-Za-z_][\w-]*)(?=[^{}]*[{,]\s*$)")]
TOKEN = re.compile(r"-{0,2}[A-Za-z_$][\w$-]*[\w$]|[A-Za-z_$][\w$]*")

# Keywords of the languages a repository commonly holds. A declaration pattern can match
# one, as `if (x) {` matches the method pattern, and a keyword is never a symbol.
KEYWORDS = frozenset("""
abstract and any as assert async await boolean break case catch class const constructor
continue debugger declare def default del delete do elif else enum except export extends
false final finally for from func function get global if implements import in instanceof
interface is keyof lambda let match module namespace never new none nonlocal not null
number object or pass private protected public raise readonly require return self set
static string struct super switch symbol this throw throws true try type typeof undefined
unknown var void while with yield
""".split())


def git(repo: str, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args[:3])} failed: {result.stderr.strip()}")
    return result.stdout


def is_generated(path: str) -> bool:
    return os.path.basename(path) in GENERATED_NAMES


def changed_files(repo: str, base: str, candidate: str) -> list[dict]:
    """One entry per changed path, from --numstat and --name-status in rename-aware form."""
    status = {}
    for line in git(repo, "diff", "--name-status", "-M", base, candidate).splitlines():
        parts = line.split("\t")
        status[parts[-1]] = parts[0][0]
    files = []
    for line in git(repo, "diff", "--numstat", "-M", base, candidate).splitlines():
        added, deleted, path = line.split("\t", 2)
        if " => " in path:
            path = re.sub(r"\{([^{}]*) => ([^{}]*)\}", r"\2", path).replace("//", "/")
            if " => " in path:
                path = path.split(" => ", 1)[1]
        binary = added == "-"
        files.append({
            "path": path, "status": status.get(path, "M"), "binary": binary,
            "generated": is_generated(path),
            "added": 0 if binary else int(added), "deleted": 0 if binary else int(deleted),
        })
    return files


def file_diff(repo: str, base: str, candidate: str, path: str) -> str:
    return git(repo, "diff", "-M", base, candidate, "--", path)


def declared_names(path: str, lines: list[str]) -> set[str]:
    """The names the given lines declare. Prose files declare nothing."""
    lower = path.lower()
    if lower.endswith(PROSE_SUFFIXES):
        return set()
    found: set[str] = set()
    for line in lines:
        for pattern in DECLARATIONS:
            found.update(pattern.findall(line))
        for group in DESTRUCTURED.findall(line) + EXPORT_LIST.findall(line):
            found.update(re.findall(NAME, group))
        if lower.endswith(STYLE_SUFFIXES):
            for pattern in STYLE_NAMES:
                found.update(pattern.findall(line))
    return {s for s in found if len(s.lstrip("-")) >= MIN_SYMBOL_LENGTH and s.lower() not in KEYWORDS}


def diff_symbols(diffs: dict[str, str]) -> dict[str, str]:
    """Each declared name mapped to added, deleted or changed, across the whole diff."""
    added: set[str] = set()
    deleted: set[str] = set()
    for path, text in diffs.items():
        lines = text.split("\n")
        added |= declared_names(path, [l[1:] for l in lines if l.startswith("+") and not l.startswith("+++")])
        deleted |= declared_names(path, [l[1:] for l in lines if l.startswith("-") and not l.startswith("---")])
    kinds = {s: "added" for s in added - deleted} | {s: "deleted" for s in deleted - added}
    return kinds | {s: "changed" for s in added & deleted}


def symbol_hits(repo: str, candidate: str, symbols: list[str], skip_paths: set[str]) -> dict[str, list[str]]:
    """Every line at the candidate that names each symbol as a whole word, outside the
    changed files. One git grep call covers every symbol, and each hit line is then
    matched back to the symbols it names. Exit 1 means no line matched, and any other
    exit stops the script."""
    hits: dict[str, list[str]] = {s: [] for s in symbols}
    if not symbols:
        return hits
    # One Perl-compatible alternation, because git grep tries a list of separate patterns
    # one by one on every line, which took minutes on a diff that declares a few hundred names.
    pattern = r"(?<![\w$-])(?:" + "|".join(re.escape(s) for s in symbols) + r")(?![\w$-])"
    result = subprocess.run(
        ["git", "-C", repo, "grep", "-n", "-I", "-P", "-f", "-", candidate, "--", "."],
        capture_output=True, text=True, encoding="utf-8", errors="replace", input=pattern + "\n",
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(f"git grep failed with exit {result.returncode}: {result.stderr.strip()}")
    wanted = set(symbols)
    prefix = candidate + ":"
    for raw in result.stdout.split("\n"):
        if not raw:
            continue
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
        path, _, rest = raw.partition(":")
        if path in skip_paths or is_generated(path):
            continue
        for s in set(TOKEN.findall(rest)) & wanted:
            hits[s].append(f"{path}:{rest.rstrip()}")
    return hits


def lines_of(text: str) -> list[str]:
    """Lines as Git numbers them: split on newline only, never on a form feed or a Unicode
    line separator."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def numbered(text: str) -> str:
    return "\n".join(f"{n:>6}  {line}" for n, line in enumerate(lines_of(text), 1))


def fence(text: str, lang: str = "") -> str:
    ticks = "```"
    while ticks in text:
        ticks += "`"
    return f"{ticks}{lang}\n{text}\n{ticks}\n"


def size(text: str) -> int:
    return len(text.encode("utf-8"))


def omitted_line(section: str, label: str, text: str) -> str:
    return f"- {section}: `{label}`, {size(text)} bytes\n"


def build(repo: str, base: str, candidate: str, max_bytes: int = DEFAULT_MAX_BYTES) -> tuple[str, dict]:
    files = changed_files(repo, base, candidate)
    reviewable = [f for f in files if not f["binary"] and not f["generated"]]
    diffs = {f["path"]: file_diff(repo, base, candidate, f["path"]) for f in reviewable}
    kinds = diff_symbols(diffs)
    rank = {"deleted": 0, "changed": 1, "added": 2}
    ordered = sorted(kinds, key=lambda s: (rank[kinds[s]], s))
    hits = symbol_hits(repo, candidate, ordered, {f["path"] for f in files})

    head = [f"# Review digest\n\nBase: `{base}`\nCandidate: `{candidate}`\n\n## Changed files\n\n"]
    for f in files:
        note = (" (binary, not included)" if f["binary"] else " (generated, not included)" if f["generated"]
                else " (deleted, diff only)" if f["status"] == "D" else "")
        head.append(f"- `{f['path']}` {f['status']} +{f['added']} -{f['deleted']}{note}\n")
    header = "".join(head)

    items: list[tuple[str, str, str]] = []  # (section, label, text), in fill order
    for f in reviewable:
        items.append(("Diff", f["path"], f"### `{f['path']}`\n\n" + fence(diffs[f["path"]].rstrip("\n"), "diff") + "\n"))
    for s in ordered:
        found = hits[s]
        if not found:
            continue
        shown = found[:MAX_HITS_PER_SYMBOL]
        more = f"\n... {len(found) - len(shown)} more lines" if len(found) > len(shown) else ""
        title = f"### `{s}` ({kinds[s]}, {len(found)} line{'s' if len(found) != 1 else ''})\n\n"
        items.append(("Symbol references", s, title + fence("\n".join(shown) + more) + "\n"))
    full = []
    for f in reviewable:
        if f["status"] == "D":
            continue
        text = git(repo, "show", f"{candidate}:{f['path']}")
        full.append(("Files at the candidate", f["path"], f"### `{f['path']}`\n\n" + fence(numbered(text)) + "\n"))
    items += sorted(full, key=lambda item: size(item[2]))

    quiet = [s for s in ordered if not hits[s]]
    refs_head = ("\n## Symbol references\n\nLines at the candidate, outside the changed files, that name a symbol "
                 f"the diff declares, at most {MAX_HITS_PER_SYMBOL} lines per symbol.\n\n")
    refs_tail = ("No reference outside the changed files: " + ", ".join(f"`{s}`" for s in quiet) + "\n") if quiet else ""
    scaffold = [header, "\n## Diff\n\n", refs_head, refs_tail, "\n## Files at the candidate\n\n", "\n## Not included\n\n"]
    # Every item's "Not included" line stays reserved until the item is placed, so the list
    # the digest ends with always fits inside the budget.
    reserve = sum(size(omitted_line(*item)) for item in items)
    free = max_bytes - size("".join(scaffold)) - size("Nothing.\n")
    kept: dict[str, list[str]] = {name: [] for name in SECTIONS}
    omitted: list[str] = []
    labels: list[str] = []
    used = 0
    for section, label, text in items:
        line = omitted_line(section, label, text)
        reserve -= size(line)
        if used + size(text) + reserve + sum(map(size, omitted)) <= free:
            kept[section].append(text)
            used += size(text)
        else:
            omitted.append(line)
            labels.append(f"{section}: {label}")

    parts = [header, "\n## Diff\n\n", *kept["Diff"], refs_head, *kept["Symbol references"], refs_tail]
    parts += ["\n## Files at the candidate\n\n", *kept["Files at the candidate"], "\n## Not included\n\n"]
    parts += omitted or ["Nothing.\n"]
    digest = "".join(parts)
    stats = {
        "bytes": size(digest), "max_bytes": max_bytes, "changed_files": len(files), "reviewable_files": len(reviewable),
        "symbols": len(ordered), "symbols_with_references": len(ordered) - len(quiet),
        "omitted_items": len(labels), "omitted": labels,
    }
    return digest, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", required=True, help="repository that holds both commits")
    parser.add_argument("--base", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--out", help="write the digest here instead of stdout")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    args = parser.parse_args(argv)
    digest, stats = build(args.repo, args.base, args.candidate, args.max_bytes)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(digest)
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(digest)
    print(json.dumps(stats), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
