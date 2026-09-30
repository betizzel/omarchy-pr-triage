# Draft PR for EFrMG/triage-o-mator

**Title:** Link PRs that rewrite the same lines and list confirmed duplicates apart from leads

## Summary

This is the change from #3, as one PR like you suggested. Candidate discovery gets a signal for PRs that rewrite the same lines of code, human-confirmed duplicates get listed separately from leads, and there's a new playbook for picking which PR to keep.

I worked on this with Claude (Opus 5.5) as my coding agent.

Closes #3

## What's changed

- **`bin/_candidates.py`**: a new `changed_lines` signal read from the cached `diff`. Two PRs are linked when they rewrite the same original lines and those lines cover enough of *both* PRs' changes (geometric mean of the two coverages, at least `--line-threshold 0.3`). Tests and docs count less, rarely touched paths count more, and diffs over 3,000 changed lines get a `discovery-feature-limit:diff` hold instead. Reviewed `duplicate-pr` decisions whose reason starts with "Duplicate of #N" are listed under `confirmed_duplicates` and no longer proposed as leads.
- **`bin/cache`**: the new `--line-threshold` option.
- **`bin/next`**: suggests the new playbook for candidate groups nobody has compared yet.
- **`prompts/review-candidate-set.md`**: new playbook for picking the PR to keep, written up as unreviewed "Duplicate of #N" proposals. Running tests is opt-in and only happens in a throwaway clone.
- **Docs**: `docs/groups.md`, `prompts/PLAYBOOK.md`, `prompts/organize-groups.md` and the README prompts table.
- **Tests**: two workflow tests in `tests/test_review_workflow.py`. The fake `gh` in `tests/support.py` can now serve a raw diff for the `Accept: application/vnd.github.diff` request.

The policy goes to `pr-candidate-sets-v2`. Groups made under v1 still validate.

## Result

Same data as in #3: all 2,849 open Omarchy PRs, checked against @aholbreich's hand-verified duplicates from omacom/omarchy#11049.

| | `master` | this PR |
| --- | --- | --- |
| Hand-verified duplicate pairs found | 64 / 166 (38.6%) | 121 / 166 (72.9%) |
| Candidate sets to read | 696 | 1,061 |

## Testing

- `make check` passes (29 tests, go vet, gopls, Prettier, gofmt)
- Both new tests fail on `master` and pass here
- Merged with `agents-in-the-loop` it merges cleanly and `make check` still passes (40 tests), so the second part shouldn't get in that branch's way
