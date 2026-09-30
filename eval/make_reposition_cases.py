#!/usr/bin/env python3
"""Turn the hand-checked duplicate groups into Reposition cache-retrieval cases.

Each case starts where a triager starts: the title of the oldest open PR in a
group, searched over summaries, file lists and diffs. The expected items are the
group's open PRs. Groups with fewer than two open PRs in the ledger are skipped.

  eval/make_reposition_cases.py --labels eval/aholbreich-2026-09-27.json \
    --ledger ~/Developer/omarchy-tom-sandbox/triage-o-mator/data/omacom/omarchy/ledger.jsonl \
    --output ~/Developer/reposition/examples/omarchy-review-cases.json
"""

import argparse
import json
import re
from pathlib import Path


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--labels", type=Path, required=True)
  parser.add_argument("--ledger", type=Path, required=True)
  parser.add_argument("--output", type=Path, required=True)
  args = parser.parse_args()

  labels = json.loads(args.labels.read_text())
  rows = [json.loads(line) for line in args.ledger.read_text().splitlines() if line.strip()]
  open_prs = {row["number"]: row for row in rows if row["kind"] == "pr" and row["state"] == "open"}

  cases, skipped = [], []
  for group in labels["duplicate_groups"]:
    members = sorted(n for n in group["prs"] if n in open_prs)
    if len(members) < 2:
      skipped.append(group["prs"])
      continue

    anchor = members[0]
    slug = re.sub(r"[^a-z0-9]+", "-", group["what"].lower()).strip("-")[:40]
    cases.append({
      "id": f"omarchy-{anchor}-{slug}",
      "query": open_prs[anchor]["title"],
      "components": ["summary", "files", "diff"],
      "max_snippets_per_item": 1,
      "expected_items": [f"pr:{n}" for n in members],
    })

  output = {
    "label_authority": "Hand-checked duplicate groups for omacom/omarchy by @aholbreich (2026-09-27 update to omacom/omarchy#11049). He found candidates by clustering titles and requiring a shared primary file, then checked each by reading the diffs, so the labels are independent of Reposition and triage-o-mator but share a lexical candidate step with this retrieval. Queries are the oldest open member's title, chosen by us, not by the labeller. Membership is limited to PRs open in the ledger used to build the cases.",
    "source": labels["source"],
    "hard_negatives": labels["hard_negatives"],
    "cases": cases,
  }
  args.output.write_text(json.dumps(output, indent=1) + "\n")
  print(f"{len(cases)} cases, {len(skipped)} groups skipped (fewer than two open PRs) -> {args.output}")


if __name__ == "__main__":
  main()
