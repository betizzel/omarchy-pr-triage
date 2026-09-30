#!/usr/bin/env python3
"""Score triage-o-mator candidate discovery against independently hand-checked duplicate pairs.

Runs `bin/cache candidates` from two triage-o-mator checkouts (for example upstream
master and a branch) against the same install and frozen corpus, then reports how
many labelled same-fix pairs each surfaces together in a candidate set.

  eval/tom_discovery_eval.py --install ~/Developer/omarchy-tom-sandbox/triage-o-mator \
    --corpus ID --labels eval/aholbreich-2026-09-27.json \
    --checkout master=/tmp/tom-master --checkout branch=~/Developer/triage-o-mator
"""

import argparse
import itertools
import json
import os
import subprocess
from pathlib import Path


def candidates(checkout, install, corpus):
  env = dict(os.environ, TRIAGE_ROOT=str(install))
  results, offset, token, as_of = [], 0, None, None
  while True:
    args = [str(checkout / "bin/cache"), "candidates", "--corpus", corpus, "--limit", "100", "--offset", str(offset)]
    if token:
      args += ["--checkpoint", token, "--as-of", as_of]
    page = json.loads(subprocess.run(args, env=env, capture_output=True, text=True, check=True).stdout)
    results += page["results"]
    token, as_of = page["checkpoint"], page["options"]["as_of"]
    if page["continuation"] is None:
      return page, results
    offset = page["continuation"]["offset"]


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--install", type=Path, required=True)
  parser.add_argument("--corpus", required=True)
  parser.add_argument("--labels", type=Path, required=True)
  parser.add_argument("--checkout", action="append", required=True, help="name=path")
  parser.add_argument("--output", type=Path)
  args = parser.parse_args()

  labels = json.loads(args.labels.read_text())
  report = {"labels": str(args.labels), "corpus": args.corpus, "runs": {}}
  for spec in args.checkout:
    name, path = spec.split("=", 1)
    last, results = candidates(Path(path).expanduser(), args.install.expanduser(), args.corpus)
    usable = {row["item"]["number"] for row in last["observations"] if not row["exclusions"]}
    sets = [row["members"] for row in results if row["type"] == "candidate-set"]
    together = {pair for members in sets for pair in itertools.combinations(sorted(members), 2)}
    excluded = {tuple(row["members"]) for row in results if row["type"] == "excluded-pair"}
    labelled = {pair for group in labels["duplicate_groups"] for pair in itertools.combinations(sorted(group["prs"]), 2)
                if pair[0] in usable and pair[1] in usable}
    found = labelled & together
    signals = {}
    for row in results:
      for pair in row.get("pairs", []):
        key = tuple(pair["members"])
        if key in labelled:
          signals[key] = sorted({signal["signal"] for signal in pair["signals"]})
    report["runs"][name] = {
      "policy": last["policy"],
      "usable_prs": len(usable),
      "candidate_sets": len(sets),
      "pairs_surfaced": len(together),
      "labelled_pairs_in_scope": len(labelled),
      "labelled_pairs_surfaced": len(found),
      "recall": round(len(found) / len(labelled), 3) if labelled else None,
      "labelled_pairs_excluded": sorted(labelled & excluded),
      "missed": sorted(labelled - together),
      "signals_on_found": {f"{a}/{b}": s for (a, b), s in sorted(signals.items())},
      "confirmed_pairs": last.get("confirmed_pairs"),
    }
    for negative in labels.get("hard_negatives", []):
      linked = sorted(n for n in negative["near"] if tuple(sorted((negative["pr"], n))) in together)
      report["runs"][name].setdefault("hard_negatives_linked", {})[negative["pr"]] = linked

  text = json.dumps(report, indent=1)
  if args.output:
    args.output.write_text(text + "\n")
  print(text)


if __name__ == "__main__":
  main()
