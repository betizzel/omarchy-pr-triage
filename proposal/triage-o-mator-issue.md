# Draft issue for EFrMG/triage-o-mator

**Title:** Candidate discovery misses duplicate PRs that change the same lines

## Summary

Hi! Jeremy pointed me here from the Omarchy triage discussion. I'd been working on my own tool for finding duplicate PRs in the Omarchy backlog, but instead of running a second tool next to yours, I'd like to add the useful parts to triage-o-mator.

`bin/cache candidates` links PRs by similar titles, shared files and shared closing issues. It misses PRs that fix the same thing with different titles, when the file they share is touched by too many PRs to count.

I worked on this with Claude (Opus 5.5) as my coding agent.

## Example

These four Omarchy PRs all fix the same stale checks in `test/cli`:

- omacom/omarchy#13065
- omacom/omarchy#13133
- omacom/omarchy#13766
- omacom/omarchy#13810

Why `master` misses them:

- Their titles are all different (title similarity 0.06 to 0.31, far below the 0.8 threshold)
- `test/cli` is touched by dozens of open PRs, so the shared-file signal is suppressed (which is the right call)
- None of them closes an issue in common

So `master` doesn't link any of them, even though all four rewrite the same lines.

## Solution

I've made the changes on a branch in my fork: [betizzel/triage-o-mator@changed-lines-signal](https://github.com/betizzel/triage-o-mator/tree/changed-lines-signal). They are:

1. **A new `changed_lines` signal.** Two PRs get linked when they rewrite the same original lines of code, using the `diff` that's already in the cache. Both PRs have to overlap enough, so a huge PR doesn't get linked to every small PR it happens to share one line with.
2. **Confirmed duplicates are listed separately from leads.** Once a human has reviewed a `duplicate-pr` decision like "Duplicate of #N", that pair shows up once under `confirmed_duplicates` and stops showing up as a lead. Unreviewed proposals stay leads.
3. **A new playbook, `review-candidate-set.md`.** It helps an agent pick which PR to keep in a set and write "Duplicate of #N" proposals for a human to review. Running tests is opt-in and only happens in a throwaway clone.

The policy version goes to `pr-candidate-sets-v2`, and groups made under v1 still work.

## Result after the change

I installed triage-o-mator into a clone of Omarchy, downloaded all 2,849 open PRs, and ran `master` and my branch on the same data. Then I checked both against @aholbreich's hand-verified duplicates from omacom/omarchy#11049 (28 groups and 27 pairs, each checked by reading the diffs).

| | `master` | my branch |
| --- | --- | --- |
| Hand-verified duplicate pairs found | 64 / 166 (38.6%) | 121 / 166 (72.9%) |
| Candidate sets to read | 696 | 1,061 |

- All 57 extra pairs come from the new signal
- The tradeoff is about 50% more candidate sets to read
- The four PRs in the example are now linked

I also tested it with:

- `make check` (everything passes)
- Two new tests in `tests/test_review_workflow.py` that fail on `master` and pass on the branch
- A reviewed duplicate on the real Omarchy data, to check it shows up as confirmed

## What it still misses

- 39 of the 45 missed pairs are PRs that *add* the same new code (like two new usage collectors), since the signal only looks at lines being rewritten
- The other 6 are missing part of their diff in the downloaded data

## Something I ran into

The bulk download stopped a few times with `GitHub returned invalid JSON`. The response was actually empty: `gh` failed on a GraphQL call but the status line said 200, so the real error gets reported as invalid JSON. Rerunning picked up where it left off each time. I can open a separate issue for this if that helps.

I'd like to make a pull request for this. Would you rather have it as one PR, or split into the new signal and the confirmed-duplicates list?
