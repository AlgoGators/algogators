# Updating the tracker page

## Rebuilding

Run `python3 tools/issue-tracker/sync.py tracker.html --standalone` after a change on GitHub to see it at once.

## After a rebuild, check the attention lists

The build prints:
- issues with no priority label;
- issues with no `project:` label;
- issues with no assignee;
- issues with no `Status:` comment;
- issues not on the order list in AlgoGators/trade-ngin#153;
- issues marked Blocked whose blockers are all closed;
- issues closed since the last full read;
- new pull requests with no disposition.

## Handling what it finds

- A new issue with no labels: agree a priority, a project and a first status comment with the issue's owner. Then add
  the labels, post the comment, and add the issue to the order list at the agreed place.
- A new pull request: agree a one-line disposition, then add it under `prs` in `base.json` by PR here.
- A new project: create the `project: <name>` label in every tracked repository, and add it to `projects` in
  `base.json` and to `PL` in `sync.py`.
- A new assignee: add the login and the name the page shows to `NAMES` in `sync.py`.
- Never close, assign, relabel or comment on someone else's issue or PR without their agreement. Ask about each pull
  request on its own before closing or merging it.

## Wording on the page

The page is shared with the whole team:
- third person and task-focused;
- no remarks about people;
- "Waiting on" holds real blockers only: another issue or PR, access, or data;
- Dependabot PRs stay on their own tab;
- status comments are the four lines and nothing else.

The QT platform is described as under thorough review by Dom, with versions to be released soon.
