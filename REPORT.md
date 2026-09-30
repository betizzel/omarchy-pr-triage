# Report: finding duplicate PRs in the Omarchy backlog

*2026-09-30. Built with Claude (Opus 5.5) as my coding agent.*

## Summary

Omarchy has about 2,850 open PRs, and many of them fix the same thing. Several people are building tools to find those duplicates, including this one. Instead of adding one more separate tool, I measured the tools against the same hand-checked data and contributed the part that helped most to [triage-o-mator](https://github.com/EFrMG/triage-o-mator), which was built for the Omarchy backlog.

The key idea: **two PRs that rewrite the same lines of code are probably fixing the same thing**, even when their titles are different and the file they share is touched by dozens of other PRs.

## The yardstick

@aholbreich posted hand-checked duplicate groups in [omacom/omarchy#11049](https://github.com/omacom/omarchy/issues/11049#issuecomment-5857142203) (2026-09-27 update): 28 groups and 27 pairs, each checked by reading the diffs. None of the tools below produced those labels, so they make a fair test. Of those pairs, 166 were still open on 2026-09-30.

One caveat: he found his candidates by clustering titles and requiring a shared primary file, then read the diffs. So the labels lean toward duplicates that title and file signals can already find. That favours the title- and file-based tools here, which makes the changed-lines gain a conservative result.

The labels only say which PRs *are* duplicates. They don't list every duplicate in the backlog, so these numbers measure how many known duplicates a tool finds, not how many wrong ones it suggests.

## Results

| Tool | Hand-checked duplicate pairs found | How it counts |
| --- | --- | --- |
| This tool (`pr_clusters.py`) | 131 / 166 (79%) | Both PRs in the same cluster |
| triage-o-mator `master` | 64 / 166 (38.6%) | Both PRs in the same candidate set |
| triage-o-mator + changed-lines signal | 121 / 166 (72.9%) | Both PRs in the same candidate set |
| Tranche v0.2.1 | 16 / 166 (9.6%) | Both PRs in a "same change" group |

The rows don't count in exactly the same way. This tool's clusters are connected groups: two PRs can share a cluster through a third PR. triage-o-mator needs a direct link between every pair in a set, which is stricter. Tranche's groups are pairs a model judged the same from titles and descriptions.

### triage-o-mator, before and after

Same 2,849 open PRs, same data, only the code changed:

- 57 more duplicate pairs found, all from the new signal
- The cost is more to read: 1,061 candidate sets instead of 696
- Example: #13065, #13133, #13766 and #13810 all fix the same `test/cli` checks with different titles. `master` links none of the six pairs; the branch links all six.
- Still missed: 39 pairs where both PRs *add* the same new code (the signal only looks at rewritten lines), and 6 with missing diff data

### reposition on the real backlog

[reposition](https://github.com/Univeracity/reposition) is Jeremy's search tool for triage-o-mator's data. I ran it for the first time on a real backlog, using the hand-checked groups as test cases. Searching with one PR's title found **75 of 91 (82.4%)** of its known duplicates within its 12 KB answer limit. A search took about 0.3 seconds, against 3.9 seconds for a plain text scan.

### Tranche

[Tranche](https://github.com/blackopsrepl/Tranche) packs PRs into batches of five using a model that reads titles and descriptions but not code. Two things worth knowing:

- 118 hand-checked duplicate pairs land in *different* batches. The four VS Code PRs above are in four separate batches (B098, B101, B106, B107), so the same fix would get reviewed four times.
- 465 of the 565 batches are five unrelated PRs, packed by security flag, risk and age.

A code-overlap signal like the one above could help here too. Tranche's issue #2 asks for a non-model fallback.

## What I contributed

| Where | What | Status |
| --- | --- | --- |
| triage-o-mator | Changed-lines signal, confirmed duplicates listed apart from leads, and a playbook for picking which PR to keep | [PR #4](https://github.com/EFrMG/triage-o-mator/pull/4) opened, after [issue #3](https://github.com/EFrMG/triage-o-mator/issues/3) |
| reposition | 55 real test cases from #11049 and the first real-backlog trial | [PR #3](https://github.com/Univeracity/reposition/pull/3) opened |
| This repo | `pr_clusters.py`, the agent review brief, the Omarchy bar plugin and the evaluation scripts | Here |

## Things I ran into

- **triage-o-mator's bulk download** stops with `GitHub returned invalid JSON` when GitHub sends back an empty response. The real error is hidden behind that message. Rerunning picks up where it stopped; it took 7 runs to download all 2,849 PRs.
- **Launching an agent from a script:** `omarchy agent prompt` stays alive as long as the agent's terminal is open. A script that waits for it with a timeout kills the agent when the timeout fires. Start it detached instead.
- **The agent review works.** On the VS Code cluster, the agent tested all four PRs in throwaway checkouts and found that #13810 is incomplete: it fixes one of the two stale checks, so `./test/cli` still fails. A reviewer's informal "treat this as an approval" comment was noted as information, not followed as an instruction, and the agent posted nothing to GitHub.

## Reproduce it

```bash
# score triage-o-mator (any two checkouts) against the hand-checked labels
eval/tom_discovery_eval.py --install /path/to/omarchy/triage-o-mator --corpus CORPUS_ID \
  --labels eval/aholbreich-2026-09-27.json --checkout master=/path/a --checkout branch=/path/b

# rebuild the reposition test cases from a triage-o-mator ledger
eval/make_reposition_cases.py --labels eval/aholbreich-2026-09-27.json \
  --ledger /path/to/ledger.jsonl --output omarchy-review-cases.json
```

Raw results are in `eval/tom-discovery-result.json`.

## Next steps

1. triage-o-mator PR in review ([#4](https://github.com/EFrMG/triage-o-mator/pull/4))
2. Reposition PR in review ([#3](https://github.com/Univeracity/reposition/pull/3))
3. Offer the code-overlap idea to Tranche on its issue #2
4. Match PRs that add the same new code, the biggest remaining gap
