# How the page is ordered and labelled

| On the page | Change it on GitHub by |
|---|---|
| Priority | the issue's `P0` to `P3` label (no label: shown as "Needs triage") |
| Project | the issue's `project: <name>` label (the first one counts) |
| Assignee | assigning the issue |
| Blocked by | the issue's "Blocked by" links |
| Status, waiting on, next step, notes | a new comment on the issue starting with `Status:`; the latest one is shown |
| Order of work | moving the issue's line in the order list, AlgoGators/trade-ngin#153 |

A status comment:

```
Status: In progress
Waiting on: #105
Next step: load the per-contract bars
Notes: anything the next person needs to know
```

Status is one of: In progress, PR open, Draft PR open, Fix written, no PR, Ready to start, Under review, Blocked,
Parked, Reference, Done.

Order of work:
- The order list's body lists issues top first, one per line, as `- #n` for trade-ngin or `- AlgoGators/<repo>#n` for
  any repository. Other lines in it are ignored.
- An issue that is not on the list is placed after the listed ones within its priority.
- Headings in the list are only for reading; priority comes from the label.

Each person's queue is built from their assigned open issues, in this order:
1. by priority;
2. by status (being worked on first, then waiting, then parked);
3. by the order list.

Issues labelled `tracker` are not shown. Unit-testing issues show in the rotation tab, not in the queues.

Migrated data-ngin issues show under their algogators numbers.
