"""Replay open trade-ngin PRs onto the imported history under services/trade-ngin.

For each PR, the commits between its merge base with trade-ngin main and its
head are rewritten the way the import was: tree wrapped under the prefix,
parents mapped (old -> rebuilt -> prefixed), AI attribution lines removed,
authors, committers and dates kept. Writes refs/replay/<n> in the repo.
Usage: replay_prs.py <repo> <commit_map.csv> <prefixed_map.csv> <PR number or branch>...
PR heads are read from refs/pr/<n> (fetch +refs/pull/*/head:refs/pr/*), branches
from origin/<name>; a branch is written to refs/replay/branch/<name>.
"""

import csv
import os
import subprocess
import sys

repo, cmap, pmap, *numbers = sys.argv[1:]
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rebuild_history import attribution_count, strip_attribution  # noqa: E402

PREFIX = ["services", "trade-ngin"]


def git(*args, inp=None):
    return subprocess.run(
        ["git", "-C", repo, *args], input=inp, capture_output=True, check=True
    ).stdout


with open(cmap, encoding="utf-8") as f:
    old_to_rebuilt = {r["old"]: r["new"] for r in csv.DictReader(f)}
with open(pmap, encoding="utf-8") as f:
    rebuilt_to_prefixed = {r["rebuilt"]: r["prefixed"] for r in csv.DictReader(f)}
mapped = {o: rebuilt_to_prefixed[r] for o, r in old_to_rebuilt.items()}


def wrap(tree):
    for name in reversed(PREFIX):
        tree = git("mktree", inp=f"040000 tree {tree}\t{name}\n".encode()).decode().strip()
    return tree


for n in numbers:
    # A number is a PR (refs/pr/<n>); anything else is a branch (origin/<name>).
    src, dst = (
        (f"refs/pr/{n}", f"refs/replay/{n}")
        if n.isdigit()
        else (f"origin/{n}", f"refs/replay/branch/{n}")
    )
    head = git("rev-parse", src).decode().strip()
    base = git("merge-base", head, "origin/main").decode().strip()
    new = dict(mapped)
    removed = 0
    for old in git("rev-list", "--topo-order", "--reverse", f"{base}..{head}").decode().split():
        raw = git("cat-file", "commit", old)
        hdr, _, msg = raw.partition(b"\n\n")
        out = []
        for ln in hdr.split(b"\n"):
            if ln.startswith(b"tree "):
                out.append(b"tree " + wrap(ln[5:].decode()).encode())
            elif ln.startswith(b"parent "):
                out.append(b"parent " + new[ln[7:].decode()].encode())
            elif ln.startswith((b"gpgsig", b" ")):
                continue  # a signature no longer matches the rewritten commit
            else:
                out.append(ln)
        text = msg.decode("utf-8")
        removed += attribution_count(text)
        text = strip_attribution(text)
        obj = b"\n".join(out) + b"\n\n" + text.encode("utf-8")
        new[old] = git("hash-object", "-t", "commit", "-w", "--stdin", inp=obj).decode().strip()
    git("update-ref", dst, new[head])
    print(
        f"{n}: base {base[:8]} -> {mapped[base][:8]}, head {new[head][:8]}, "
        f"attribution lines removed {removed}"
    )
