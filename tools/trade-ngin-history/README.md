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
