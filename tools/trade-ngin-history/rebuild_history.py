"""Rebuild trade-ngin's main and main-rolls as one commit per pull request (#90).

Local only: it reads a clone of AlgoGators/trade-ngin, writes new commits into that
clone's object store and the refs refs/rebuilt/<branch>, and never pushes.

What it does, on main's first-parent history from the root:
- a pull request merged as a merge commit ("Merge pull request #N from ...") becomes one
  commit: the merge's tree, the pull request's title and number as the subject;
- a branch rejoin named in --keep-merge (#237 main-nonQT, #282 main-rolls) stays a merge,
  and that branch's own first-parent history is rebuilt the same way, so its per-PR
  commits stay reachable;
- a merge that is not a pull request (a `git pull` merge or a branch merged by hand, from
  before pull requests were the rule) stays a merge the same way, so the commits it
  brought in keep their authors; --collapse-other-merges collapses them instead;
- every other commit keeps its message;
- in every message, AI attribution lines (Co-Authored-By: Claude..., Claude-Session:,
  "Generated with Claude Code", claude.ai session links) are removed;
- authors, committers and dates are kept; every tree is the original commit's tree.

It writes, in --out:
- commit_map.csv: old commit, new commit, tree, kind, subject, one row per rebuilt commit;
- proof.txt: the tree check of every row, the tip checks, the attribution count before and
  after, and the commit counts.

Usage:
    python rebuild_history.py --repo PATH_TO_TRADE_NGIN_CLONE --out OUT_DIR
        [--main origin/main] [--branch main-rolls=origin/main-rolls]
        [--keep-merge a5072f04] [--keep-merge 92dcb3cf] [--titles titles.json]

--titles is a JSON object {"pr number": "title"}; a merge whose message carries no title
line is looked up there, then with `gh pr view` (read-only).
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys

ATTRIBUTION = [
    re.compile(r"^\s*Co-Authored-By:.*(claude|anthropic)", re.I),
    re.compile(r"^\s*Claude-Session:", re.I),
    re.compile(r"Generated with \[?Claude Code", re.I),
    re.compile(r"^\s*https://claude\.ai/code/session_\S*\s*$", re.I),
]
PR_MERGE = re.compile(r"^Merge pull request #(\d+) from (\S+)")


def git(repo, *args, env=None, inp=None):
    out = subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        input=inp,
    )
    if out.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout


def strip_attribution(message):
    kept = [line for line in message.split("\n") if not any(p.search(line) for p in ATTRIBUTION)]
    text = "\n".join(kept)
    text = re.sub(r"\n{3,}", "\n\n", text).strip("\n")
    return text + "\n"


def attribution_count(message):
    return sum(1 for line in message.split("\n") if any(p.search(line) for p in ATTRIBUTION))


class Rebuilder:
    def __init__(self, repo, keep_merges, titles, collapse_other=False):
        self.repo = repo
        self.collapse_other = collapse_other
        self.keep = {git(repo, "rev-parse", k).strip() for k in keep_merges}
        self.titles = titles
        self.map = {}
        self.rows = []
        self.before = 0
        self.after = 0

    def info(self, sha):
        raw = git(
            self.repo,
            "show",
            "-s",
            "--format=%T%x00%P%x00%an%x00%ae%x00%aI%x00%cn%x00%ce%x00%cI%x00%B",
            sha,
        )
        tree, parents, an, ae, ad, cn, ce, cd, body = raw.split("\x00", 8)
        return {
            "tree": tree,
            "parents": parents.split(),
            "an": an,
            "ae": ae,
            "ad": ad,
            "cn": cn,
            "ce": ce,
            "cd": cd,
            "msg": body,
        }

    def pr_title(self, number, msg):
        lines = [line.strip() for line in msg.split("\n")[1:] if line.strip()]
        if lines and not any(p.search(lines[0]) for p in ATTRIBUTION):
            return lines[0], "\n".join(lines[1:])
        if str(number) in self.titles:
            return self.titles[str(number)], ""
        out = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(number),
                "--repo",
                "AlgoGators/trade-ngin",
                "--json",
                "title",
                "-q",
                ".title",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if out.returncode != 0 or not out.stdout.strip():
            sys.exit(f"no title for pull request #{number}; add it to --titles")
        return out.stdout.strip(), ""

    def side_authors(self, base, side):
        """The human authors of a pull request's own commits, most commits first (an AI
        author, or a name only in an AI trailer, is not one)."""
        counts, first, emails = {}, {}, {}
        rows = git(self.repo, "log", "--reverse", "--format=%an%x00%ae", f"{base}..{side}").split(
            "\n"
        )
        for k, row in enumerate(r for r in rows if r):
            name, email = row.split("\x00")
            if re.search(r"claude|anthropic", name + email, re.I):
                continue
            counts[name] = counts.get(name, 0) + 1
            first.setdefault(name, k)
            emails.setdefault(name, email)
        order = sorted(counts, key=lambda n: (-counts[n], first[n]))
        return [(n, emails[n]) for n in order]

    def chain(self, tip, stop=None):
        """First-parent commits from (stop, tip], oldest first."""
        spec = f"{stop}..{tip}" if stop else tip
        return git(self.repo, "rev-list", "--first-parent", "--reverse", spec).split()

    def rebuild(self, tip, stop=None):
        new_parent = self.map.get(stop) if stop else None
        for sha in self.chain(tip, stop):
            if sha in self.map:
                new_parent = self.map[sha]
                continue
            c = self.info(sha)
            self.before += attribution_count(c["msg"])
            parents = [new_parent] if new_parent else []
            subject = c["msg"].split("\n", 1)[0]
            m = PR_MERGE.match(subject)
            if len(c["parents"]) > 1 and (sha in self.keep or (not m and not self.collapse_other)):
                side = c["parents"][1]
                base = git(self.repo, "merge-base", c["parents"][0], side).strip()
                self.rebuild(side, base)
                parents.append(self.map[side])
                kind = "kept merge" if sha in self.keep else "kept merge, not a pull request"
                msg = strip_attribution(c["msg"])
            elif len(c["parents"]) > 1 and m:
                title, rest = self.pr_title(m.group(1), c["msg"])
                kind = "pr merge"
                # The work's authors, not the person who merged it: the author with the most
                # commits in the pull request is the commit's author, the others co-authors.
                authors = self.side_authors(c["parents"][0], c["parents"][1])
                if authors:
                    c["an"], c["ae"] = authors[0]
                trailers = "\n".join(f"Co-authored-by: {n} <{e}>" for n, e in authors[1:])
                msg = strip_attribution(
                    f"{title} (#{m.group(1)})"
                    + (f"\n\n{rest}" if rest else "")
                    + (f"\n\n{trailers}" if trailers else "")
                )
            elif len(c["parents"]) > 1:
                kind, msg = "other merge, first parent kept", strip_attribution(c["msg"])
            else:
                kind, msg = "commit", strip_attribution(c["msg"])
            self.after += attribution_count(msg)
            env = dict(
                os.environ,
                GIT_AUTHOR_NAME=c["an"],
                GIT_AUTHOR_EMAIL=c["ae"],
                GIT_AUTHOR_DATE=c["ad"],
                GIT_COMMITTER_NAME=c["cn"],
                GIT_COMMITTER_EMAIL=c["ce"],
                GIT_COMMITTER_DATE=c["cd"],
            )
            args = ["commit-tree", c["tree"]]
            for p in parents:
                args += ["-p", p]
            new = git(self.repo, *args, env=env, inp=msg).strip()
            self.map[sha] = new
            self.rows.append(
                {
                    "old": sha,
                    "new": new,
                    "tree": c["tree"],
                    "kind": kind,
                    "subject": msg.split("\n", 1)[0],
                }
            )
            new_parent = new
        return self.map[git(self.repo, "rev-parse", tip).strip()]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--main", default="origin/main")
    ap.add_argument("--branch", action="append", default=[], help="name=ref, rebuilt after main")
    ap.add_argument("--keep-merge", action="append", default=[])
    ap.add_argument("--titles")
    ap.add_argument(
        "--collapse-other-merges",
        action="store_true",
        help="collapse merges that are not pull requests to their first parent too "
        "(their side commits leave the history); by default they stay merges",
    )
    a = ap.parse_args()
    titles = {}
    if a.titles:
        with open(a.titles, encoding="utf-8") as fh:
            titles = json.load(fh)
    os.makedirs(a.out, exist_ok=True)

    r = Rebuilder(a.repo, a.keep_merge, titles, a.collapse_other_merges)
    tips = {"main": (a.main, r.rebuild(a.main))}
    for spec in a.branch:
        name, ref = spec.split("=", 1)
        tips[name] = (ref, r.rebuild(ref))
    for name, (_ref, new) in tips.items():
        git(a.repo, "update-ref", f"refs/rebuilt/{name}", new)

    lines, bad = [], 0
    for row in r.rows:
        new_tree = git(a.repo, "rev-parse", f"{row['new']}^{{tree}}").strip()
        old_tree = git(a.repo, "rev-parse", f"{row['old']}^{{tree}}").strip()
        if new_tree != old_tree:
            bad += 1
            lines.append(f"TREE DIFFERS {row['old']} -> {row['new']}")
    for name, (ref, new) in tips.items():
        same = (
            git(a.repo, "rev-parse", f"{ref}^{{tree}}").strip()
            == git(a.repo, "rev-parse", f"{new}^{{tree}}").strip()
        )
        lines.append(
            f"tip {name}: {git(a.repo, 'rev-parse', ref).strip()} -> {new}, tree identical: {same}"
        )
        bad += 0 if same else 1
        lines.append(
            f"  first-parent commits: {len(git(a.repo, 'rev-list', '--first-parent', ref).split())} -> "
            f"{len(git(a.repo, 'rev-list', '--first-parent', new).split())}; "
            f"all reachable: {len(git(a.repo, 'rev-list', ref).split())} -> {len(git(a.repo, 'rev-list', new).split())}"
        )
    left = sum(
        attribution_count(git(a.repo, "show", "-s", "--format=%B", new))
        for new in git(a.repo, "rev-list", *[n for _r, n in tips.values()]).split()
    )

    def people(*refs):
        names = set(git(a.repo, "log", "--format=%an", *refs).split("\n"))
        trailer = re.compile(r"^\s*Co-authored-by:\s*(.+?)\s*<", re.I | re.M)
        names |= set(trailer.findall(git(a.repo, "log", "--format=%B", *refs)))
        return {n for n in names if n and not re.search(r"claude|anthropic", n, re.I)}

    lost = people(*[ref for ref, _n in tips.values()]) - people(*[n for _r, n in tips.values()])
    lines.append(
        f"people credited (author or co-author) in the old history but not the rebuilt one: "
        f"{sorted(lost) or 'none'}"
    )
    bad += len(lost)
    kinds = {}
    for row in r.rows:
        kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
    lines += [
        f"rebuilt commits: {len(r.rows)} ({', '.join(f'{v} {k}' for k, v in sorted(kinds.items()))})",
        f"trees checked: {len(r.rows)}, differing: {bad}",
        f"attribution lines in the rebuilt messages' sources: {r.before}; in the rebuilt history: {left}",
    ]
    with open(os.path.join(a.out, "commit_map.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["old", "new", "tree", "kind", "subject"])
        w.writeheader()
        w.writerows(r.rows)
    with open(os.path.join(a.out, "proof.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines[-3:] + [line for line in lines if line.startswith("tip ")]))
    sys.exit(1 if bad or left else 0)


if __name__ == "__main__":
    main()
