# Issue text snapshot (#90)

`snapshot.py` records every issue, pull request, comment and review comment body in the
AlgoGators organisation. It finds the texts that change when issues move between
repositories.

When the AlgoLens issues were transferred, GitHub rewrote the bare references (`#24`) of
every text that linked to one of them, with no edit history. It is read-only: it only calls
`gh api`.

```
python tools/issue-snapshot/snapshot.py snapshot --out before.json          # before a move
python tools/issue-snapshot/snapshot.py audit --repo trade-ngin --snapshot before.json --out audit.md
python tools/issue-snapshot/snapshot.py diff --before before.json --out diff.md   # after it
```

- **`snapshot`**: every text of every repository, as JSON. It takes about a minute for the
  organisation.
- **`audit`**: the texts outside a repository that a move of its issues would re-read. A text
  counts when it links in full to one of that repository's issues and also holds bare
  references. Pull requests do not move, so links to them are not counted.
- **`diff`**: every text whose body differs between the snapshot and the live texts (or a
  second snapshot), with both versions and the last-edit time. It exits non-zero when any
  text changed.

A snapshot holds the organisation's issue texts. Keep it out of the repository and out of
public places.
