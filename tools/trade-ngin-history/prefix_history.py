"""Rewrite a history so every tree sits under a prefix (services/trade-ngin).

Each commit's raw object is copied byte for byte except its tree and parent lines:
the tree becomes the wrapped tree, the parents the rewritten parents. Authors,
committers, dates and messages are untouched, so the output is deterministic.
Usage: prefix_history.py <repo> <tip> <ref-to-write> <map.csv>
"""

import subprocess
import sys

repo, tip, out_ref, map_path = sys.argv[1:5]
PREFIX = ["services", "trade-ngin"]


def git(*args, inp=None):
    return subprocess.run(
        ["git", "-C", repo, *args], input=inp, capture_output=True, check=True
    ).stdout


wrapped = {}


def wrap(tree):
    if tree not in wrapped:
        t = tree
        for name in reversed(PREFIX):
            t = git("mktree", inp=f"040000 tree {t}\t{name}\n".encode()).decode().strip()
        wrapped[tree] = t
    return wrapped[tree]


new = {}
tip = git("rev-parse", tip).decode().strip()
order = git("rev-list", "--topo-order", "--reverse", tip).decode().split()
for old in order:
    raw = git("cat-file", "commit", old)
    head, _, msg = raw.partition(b"\n\n")
    lines = head.split(b"\n")
    out = []
    for ln in lines:
        if ln.startswith(b"tree "):
            out.append(b"tree " + wrap(ln[5:].decode()).encode())
        elif ln.startswith(b"parent "):
            out.append(b"parent " + new[ln[7:].decode()].encode())
        elif ln.startswith((b"gpgsig", b" ")):
            raise SystemExit(f"{old}: signed or multi-line header, not handled")
        else:
            out.append(ln)
    obj = b"\n".join(out) + b"\n\n" + msg
    new[old] = git("hash-object", "-t", "commit", "-w", "--stdin", inp=obj).decode().strip()

git("update-ref", out_ref, new[tip])
with open(map_path, "w", encoding="utf-8", newline="\n") as f:
    f.write("rebuilt,prefixed\n")
    for old in order:
        f.write(f"{old},{new[old]}\n")
print(len(order), "commits;", out_ref, "=", new[tip])
