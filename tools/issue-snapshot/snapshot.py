"""Snapshot, diff and audit the issue and pull request texts of the organisation (#90).

Moving issues between repositories rewrites other texts without an edit history: when the
AlgoLens issues were transferred, GitHub rewrote the bare references (#24) of every text that
linked to one of them as if they were written in AlgoLens. This tool records every text
before a move and finds what changed after it. It is read-only: it only calls `gh api`.

Modes:
    snapshot --out FILE              every issue, pull request, comment and review comment
                                     body of every repository in the organisation, as JSON
    diff --before FILE [--after FILE]
                                     compare a snapshot with the live texts (or a second
                                     snapshot): every text whose body changed, with both
                                     versions and its last-edit time, so a human edit can be
                                     told from a rewrite
    audit --repo NAME [--snapshot FILE]
                                     the texts outside NAME that a move of NAME's issues would
                                     re-read: each text that links in full to a NAME issue (not
                                     a pull request; those do not move) and also holds bare
                                     references (#N), with those references

Usage:
    python snapshot.py snapshot --out before.json
    python snapshot.py audit --repo trade-ngin --snapshot before.json
    python snapshot.py diff --before before.json --out diff.md
"""

import argparse
import datetime as dt
import json
import re
import subprocess
import sys

ORG = "AlgoGators"
BARE = re.compile(r"(?<![\w/#-])#(\d+)\b")


def gh(path):
    """Every page of a REST list endpoint, as one list."""
    out = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", path],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if out.returncode != 0:
        sys.exit(f"gh api {path} failed: {out.stderr.strip()}")
    pages = json.loads(out.stdout or "[]")
    return [item for page in pages for item in (page if isinstance(page, list) else [page])]


def repositories():
    return sorted(r["name"] for r in gh(f"orgs/{ORG}/repos?per_page=100&type=all"))


def take_snapshot(repos=None):
    texts = {}
    for repo in repos or repositories():
        for i in gh(f"repos/{ORG}/{repo}/issues?state=all&per_page=100"):
            kind = "pr" if "pull_request" in i else "issue"
            texts[f"{repo}#{i['number']}"] = {
                "repo": repo,
                "kind": kind,
                "number": i["number"],
                "title": i["title"],
                "body": i.get("body") or "",
                "updated": i["updated_at"],
                "url": i["html_url"],
            }
        for c in gh(f"repos/{ORG}/{repo}/issues/comments?per_page=100"):
            number = int(c["issue_url"].rsplit("/", 1)[1])
            texts[f"{repo}#{number}/comment/{c['id']}"] = {
                "repo": repo,
                "kind": "comment",
                "number": number,
                "body": c.get("body") or "",
                "updated": c["updated_at"],
                "url": c["html_url"],
            }
        for c in gh(f"repos/{ORG}/{repo}/pulls/comments?per_page=100"):
            number = int(c["pull_request_url"].rsplit("/", 1)[1])
            texts[f"{repo}#{number}/review-comment/{c['id']}"] = {
                "repo": repo,
                "kind": "review comment",
                "number": number,
                "body": c.get("body") or "",
                "updated": c["updated_at"],
                "url": c["html_url"],
            }
    return {
        "org": ORG,
        "taken": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "texts": texts,
    }


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def diff(before, after):
    changed, gone = [], []
    for key, old in before["texts"].items():
        new = after["texts"].get(key)
        if new is None:
            gone.append(key)
        elif new["body"] != old["body"]:
            changed.append((key, old, new))
    lines = [
        f"# Text diff: snapshot of {before['taken']} against {after['taken']}",
        "",
        f"{len(changed)} texts changed, {len(gone)} no longer found.",
        "",
    ]
    for key, old, new in changed:
        lines += [
            f"## {key}",
            f"{new['url']} (last updated {new['updated']})",
            "",
            "Before:",
            "```",
            old["body"],
            "```",
            "After:",
            "```",
            new["body"],
            "```",
            "",
        ]
    if gone:
        lines += ["## Not found any more", *[f"- {k}" for k in gone]]
    return "\n".join(lines) + "\n", len(changed)


def audit(snapshot, repo):
    full = re.compile(
        rf"\b{ORG}/{re.escape(repo)}#(\d+)\b|github\.com/{ORG}/{re.escape(repo)}/(?:issues|pull)/(\d+)",
        re.I,
    )
    # Only issues move (pull requests cannot), so only a link to one of the repository's
    # issues makes GitHub re-read a text.
    moving = {
        t["number"]
        for t in snapshot["texts"].values()
        if t["repo"] == repo and t["kind"] == "issue"
    }
    rows, refs = [], 0
    for key, t in sorted(snapshot["texts"].items()):
        if t["repo"] == repo:
            continue
        linked = {int(a or b) for a, b in full.findall(t["body"])}
        if not linked & moving:
            continue
        bare = [m.group(0) for m in BARE.finditer(t["body"])]
        if bare:
            rows.append((key, t["url"], bare))
            refs += len(bare)
    items = {key.split("/")[0] for key, _u, _b in rows}
    lines = [
        f"# Texts a move of {repo}'s issues would re-read",
        "",
        f"{refs} bare references in {len(rows)} texts of {len(items)} items outside {repo} "
        f"(snapshot of {snapshot['taken']}).",
        "",
    ]
    lines += [
        f"- {key} ({len(b)}): {' '.join(sorted(set(b), key=lambda s: int(s[1:])))} {url}"
        for key, url, b in rows
    ]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    s = sub.add_parser("snapshot")
    s.add_argument("--out", required=True)
    s.add_argument("--repo", action="append", help="only these repositories")
    d = sub.add_parser("diff")
    d.add_argument("--before", required=True)
    d.add_argument("--after", help="a second snapshot; the live texts when left out")
    d.add_argument("--out")
    a = sub.add_parser("audit")
    a.add_argument("--repo", required=True)
    a.add_argument("--snapshot", help="a snapshot; the live texts when left out")
    a.add_argument("--out")
    args = ap.parse_args()

    if args.mode == "snapshot":
        snap = take_snapshot(args.repo)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(snap, fh, ensure_ascii=False, indent=0)
        print(
            f"{len(snap['texts'])} texts from {len({t['repo'] for t in snap['texts'].values()})} repositories"
        )
        return
    if args.mode == "diff":
        before = load(args.before)
        repos = sorted({t["repo"] for t in before["texts"].values()})
        after = load(args.after) if args.after else take_snapshot(repos)
        report, n = diff(before, after)
    else:
        report = audit(load(args.snapshot) if args.snapshot else take_snapshot(), args.repo)
        n = 0
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report)
        print(report.split("\n")[2])
    else:
        sys.stdout.write(report)
    sys.exit(1 if n else 0)


if __name__ == "__main__":
    main()
