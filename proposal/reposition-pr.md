# Draft PR for Univeracity/reposition

**Title:** Add a first real-backlog trial with independently checked Omarchy cases

## Summary

This runs Reposition's cache workflow on a real backlog for the first time: all open PRs of omacom/omarchy, downloaded with triage-o-mator. The test cases come from hand-checked duplicate groups that neither Reposition nor triage-o-mator produced, which covers part of roadmap steps 1 and 2.

I worked on this with Claude (Opus 5.5) as my coding agent.

## What's added

- `examples/omarchy-review-cases.json`: 55 cases built from @aholbreich's hand-checked duplicate groups in omacom/omarchy#11049 (2026-09-27 update, each checked by reading the diffs). Each query is the title of the oldest open PR in a group, searched over `summary`, `files` and `diff`, and the expected items are the group's open PRs. The two PRs he named as the same problem solved a different way are kept as `hard_negatives` for later.
- `validation/omarchy-backlog-trial.json`: a compact receipt (16 KB) with the corpus shape, commits, index costs and one row per case.
- `docs/real-backlog-trial.md`: what was run, the results and the limits.
- `docs/roadmap.md`: a short note under steps 1 and 2 on what this covers and what is still open.

No cache or index is committed. The case file only holds PR numbers and titles, which are public.

## Results

2,849 open PRs (2,643 complete, 206 with gaps), `benchmarks/cache_retrieval.py` with its default 12,000-byte response budget:

| | |
| --- | ---: |
| Other group members returned (the anchor PR itself excluded) | 75 / 91 (82.4%) |
| Cases where every member was returned | 46 / 55 |
| Median returned items per case | 5 |
| Median warm query | 318 ms |
| Median verified literal scan, same projections | 3,864 ms |
| Indexed source / index size | 103.3 / 192.6 MiB |
| Build time / peak RSS | 26.9 s / 679 MiB |

- The anchor's own title always finds the anchor, so the 82.4% leaves it out
- Every case hit the 1,000-candidate limit, since title words use any-term matching
- Precision isn't measured: the labels say which PRs belong together, not how relevant everything else returned is

## Testing

- `python -m unittest discover -s tests` passes (79 tests, 5 skipped)
- `ruff check .` and `ruff format --check .` pass
- Both JSON files load

## One thing to check

`AGENTS.md` says to use synthetic fixtures in source control. I read that as keeping real caches and private data out, and these cases are labels over public PRs, but you know the intent better. If you'd rather keep real data out of the repo, I can move the cases and receipt to a separate place and just link them from the doc.
