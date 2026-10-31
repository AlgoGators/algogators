# trade-ngin history rebuild (#90)

`rebuild_history.py` rebuilds trade-ngin's `main` and `main-rolls` as one commit per pull
request, before the move into the mono repo. It works on a local clone, writes the rebuilt
branches to `refs/rebuilt/<branch>` there, and never pushes.

```
git clone https://github.com/AlgoGators/trade-ngin.git tn && git -C tn fetch --tags
python tools/trade-ngin-history/rebuild_history.py --repo tn --out out \
    --branch main-rolls=origin/main-rolls --keep-merge a5072f04 --keep-merge 92dcb3cf
```

What the rebuilt history is:

- **A pull request merged as a merge commit** becomes one commit: the merge's tree, with
  "title (#N)" as the subject.
  - Its author is whoever wrote the most commits in the pull request.
  - The pull request's other authors become `Co-authored-by` lines.
  - Whoever merged it stays as committer.
- **The two branch rejoins** named with `--keep-merge` stay merges, and those branches are
  rebuilt the same way:
  - #237 (main-nonQT, already one squash commit per pull request);
  - #282 (main-rolls).
- **Merges that are not pull requests** stay merges too, so the commits they brought in
  keep their authors. These are `git pull` merges and branches merged by hand before pull
  requests were the rule. `--collapse-other-merges` collapses them instead.
- **AI attribution lines** are removed from every message: Co-Authored-By Claude,
  Claude-Session, "Generated with Claude Code" and session links.
- **Every tree is the original commit's tree.**

The script exits non-zero unless all of these hold:

- every rebuilt commit's tree equals its original's;
- both tips are tree-identical;
- no attribution line is left;
- everyone credited in the old history is credited in the new one.

It writes `out/commit_map.csv` (old commit, new commit, tree, kind, subject) and
`out/proof.txt`. The rebuild is deterministic: the same input gives the same commits.

Pushing the rebuilt branches needs an agreed time and an admin to open a force-push window
on trade-ngin's `main`. That step is in #90 and is not part of this script.

## Importing into the mono repo

trade-ngin itself is not rewritten (decision on #90): the rebuilt history goes only into
this repository.

```
python tools/trade-ngin-history/prefix_history.py tn refs/rebuilt/main refs/prefixed/main out/prefixed_map.csv
git -C tn fetch origin '+refs/pull/*/head:refs/pr/*'
python tools/trade-ngin-history/replay_prs.py tn out/commit_map.csv out/prefixed_map.csv 283 258 regime/m13-live-state
```

- **`prefix_history.py`** moves every rebuilt commit's tree under `services/trade-ngin/`.
  It changes nothing else: the message, author, committer and dates stay as they are. The
  import merges this history.
- **`replay_prs.py`** rewrites an open pull request (or a branch) the same way.
  - It starts from the PR's merge base with trade-ngin `main`. That base maps to its
    rebuilt, prefixed commit, so the replay applies to the imported history with no
    conflicts of its own.
  - Its tree under the prefix equals the PR head's tree.
  - AI attribution lines are removed.
  - It writes `refs/replay/<n>`, or `refs/replay/branch/<name>` for a branch.

Both scripts are deterministic, so a rerun on the same refs gives the same commits.
