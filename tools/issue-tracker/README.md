# AlgoGators issue tracker

One page listing every open issue and pull request across trade-ngin, algogators, algosystem and algogauge (AlgoLens
is still read, though its issues now live in algogators).

GitHub is the source. The script reads issues, pull requests, labels, assignees, "Blocked by" links and the latest
comment that starts with `Status:`, and writes one HTML file. It writes nothing to GitHub.

## Build the page

You need Python 3 and the GitHub CLI, logged in (`gh auth login`); nothing else.

```
python3 tools/issue-tracker/sync.py tracker.html --standalone
```

Open `tracker.html` in a browser. Without `--standalone` the output is the page body only, for embedding.

The build prints the lists that need attention: issues with no priority label, no `project:` label, no assignee or no
`Status:` comment, issues missing from the order list, issues marked Blocked whose blockers are all closed, and new pull
requests with no disposition. `UPDATING.md` says how to handle each.

Where the built page is published for the team is still open: this repository's Pages site serves algogators.com, so it
cannot also serve the tracker. It is a box on the migration checklist issue.

## Files

| File | Role |
|---|---|
| `sync.py` | Builds the page from GitHub. Read-only. |
| `template.html` | The page; `sync.py` replaces `__DATA__` with the data. |
| `base.json` | The project list, the unit-testing rotation and the dispositions of open pull requests. No issue text: each issue's own body is its description. |
| `UPDATING.md` | How to update the page and handle what the build reports. |
| `ORDER_AND_LABELS.md` | How the page is ordered and labelled, and what to change on GitHub to change it. |

To change what the page shows, change it on GitHub (see `ORDER_AND_LABELS.md`). To change the layout, the project list
or the rotation, open a PR here.
