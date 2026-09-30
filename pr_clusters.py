#!/usr/bin/env python3
"""Cluster open GitHub PRs that change the same code, with auditable evidence.

  ./pr_clusters.py build            fetch open PRs, analyze diffs, write out/
  ./pr_clusters.py show 13810       print the cluster containing a PR
  ./pr_clusters.py agent 13810      have your default coding agent review that cluster

See README.md for how to read the output.
"""
import argparse
import collections
import concurrent.futures as cf
import datetime
import html
import json
import math
import os
import re
import shlex
import signal
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCHEMA_VERSION = 1

# A PR whose diff is larger than this is recorded but not line-analyzed: bulk
# rewrites share lines with everything and would glue unrelated PRs together.
LARGE_DIFF_LINES = 3000
# Hunks whose base-file line ranges are within this many lines count as overlapping.
HUNK_TOLERANCE = 3
# Clusters bigger than this are re-split using only their stronger edges.
MAX_CLUSTER = 12

STOPWORDS = set("""a an and the to of in on for with from by at as is are be it its this that into
when not no use using add adds added fix fixes fixed update updates make makes allow support""".split())


def run(args, cwd=None, check=True):
  proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, errors="replace")
  if check and proc.returncode != 0:
    sys.exit(f"command failed: {' '.join(args[:6])}...\n{proc.stderr.strip()}")
  return proc


# ---------------------------------------------------------------- fetching

def fetch_metadata(repo):
  fields = "number,title,url,author,createdAt,updatedAt,isDraft,baseRefName,headRefOid,labels,reviewDecision,mergeable,closingIssuesReferences"
  out = run(["gh", "pr", "list", "-R", repo, "--state", "open", "--limit", "10000", "--json", fields]).stdout
  return json.loads(out)


def sync_mirror(repo, mirror, prs):
  """Fetch every base branch and PR head into a private bare repo (never your checkout)."""
  url = f"https://github.com/{repo}.git"
  if not mirror.exists():
    run(["git", "init", "--bare", "-q", str(mirror)])

  base_shas = {}
  for base in sorted({p["baseRefName"] for p in prs}):
    if run(["git", "fetch", "-q", "--no-tags", url, f"+refs/heads/{base}:refs/heads/{base}"], cwd=mirror, check=False).returncode == 0:
      base_shas[base] = run(["git", "rev-parse", f"refs/heads/{base}"], cwd=mirror).stdout.strip()

  have = set(run(["git", "for-each-ref", "--format=%(objectname) %(refname)", "refs/pr"], cwd=mirror).stdout.splitlines())
  need = [p for p in prs if f"{p['headRefOid']} refs/pr/{p['number']}" not in have]
  print(f"fetching {len(need)} changed PR heads ({len(prs) - len(need)} cached)", file=sys.stderr)
  for i in range(0, len(need), 300):
    specs = [f"+refs/pull/{p['number']}/head:refs/pr/{p['number']}" for p in need[i:i + 300]]
    run(["git", "fetch", "-q", "--no-tags", url, *specs], cwd=mirror)
  return base_shas


# ---------------------------------------------------------------- per-PR analysis

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")


def normalize_line(line):
  line = " ".join(line.split())
  # Lines like "fi", "}", "done" appear everywhere and prove nothing.
  return line if len(line) >= 12 else None


def analyze_pr(mirror, pr, base_shas):
  number, base = pr["number"], pr["baseRefName"]
  info = {"error": None, "merge_base": None, "large": False,
          "files": {}, "additions": 0, "deletions": 0}
  if base not in base_shas:
    info["error"] = f"base branch {base} not found"
    return number, info

  head = f"refs/pr/{number}"
  mb = run(["git", "merge-base", f"refs/heads/{base}", head], cwd=mirror, check=False)
  if mb.returncode != 0:
    info["error"] = "no merge base with base branch"
    return number, info
  info["merge_base"] = mb.stdout.strip()

  diff = run(["git", "diff", "--no-color", "--no-ext-diff", "-U0", info["merge_base"], head], cwd=mirror, check=False).stdout
  path = old_path = None
  for line in diff.splitlines():
    if line.startswith("--- "):
      old_path = line[6:] if line.startswith("--- a/") else None
    elif line.startswith("+++ "):
      path = line[6:] if line.startswith("+++ b/") else old_path
      if path:
        info["files"].setdefault(path, {"additions": 0, "deletions": 0, "hunks": [], "removed": set()})
    elif path and line.startswith("@@"):
      m = HUNK_RE.match(line)
      if m:
        start, count = int(m[1]), int(m[2] if m[2] is not None else 1)
        info["files"][path]["hunks"].append((start, start + max(count, 1) - 1))
    elif path and line.startswith("+") and not line.startswith("+++"):
      info["files"][path]["additions"] += 1
    elif path and line.startswith("-") and not line.startswith("---"):
      info["files"][path]["deletions"] += 1
      norm = normalize_line(line[1:])
      if norm:
        info["files"][path]["removed"].add(norm)

  info["additions"] = sum(f["additions"] for f in info["files"].values())
  info["deletions"] = sum(f["deletions"] for f in info["files"].values())
  if info["additions"] + info["deletions"] > LARGE_DIFF_LINES:
    info["large"] = True
    for f in info["files"].values():
      f["hunks"], f["removed"] = [], set()
  return number, info


# ---------------------------------------------------------------- similarity

def title_tokens(title):
  words = [w.rstrip("s") if len(w) > 4 else w for w in re.findall(r"[a-z0-9]+", title.lower())]
  return [w for w in words if len(w) >= 3 and w not in STOPWORDS]


def title_vectors(prs):
  tokens = {p["number"]: title_tokens(p["title"]) for p in prs}
  df = collections.Counter(w for ws in tokens.values() for w in set(ws))
  n = len(prs)
  vectors = {}
  for number, ws in tokens.items():
    vec = {w: math.log(n / df[w]) for w in set(ws)}
    norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    vectors[number] = {w: v / norm for w, v in vec.items()}
  return vectors


def cosine(a, b):
  if len(a) > len(b):
    a, b = b, a
  return sum(v * b.get(w, 0.0) for w, v in a.items())


def ranges_touch(a, b):
  return a[0] <= b[1] + HUNK_TOLERANCE and b[0] <= a[1] + HUNK_TOLERANCE


# Tests and docs ride along with most fixes, so sharing one says little about
# whether two PRs fix the same thing; the primary file (the script or QML being
# fixed) does. Hand triage in omacom/omarchy#11049 excludes them for the same
# reason; here they still count, at a fifth of the weight.
SECONDARY_WEIGHT = 0.2
SECONDARY_RE = re.compile(r"^(test|docs|manual)/|\.md$|README")


def is_secondary(path):
  return bool(SECONDARY_RE.search(path))


def compare(a, b, ia, ib, idf, titles, closes):
  shared = sorted(set(ia["files"]) & set(ib["files"]))
  same_lines, overlapping = [], []
  for path in shared:
    fa, fb = ia["files"][path], ib["files"][path]
    common = fa["removed"] & fb["removed"]
    if common:
      same_lines.append({"path": path, "count": len(common), "sample": min(common, key=len)[:160]})
    for ra in fa["hunks"]:
      hit = next((rb for rb in fb["hunks"] if ranges_touch(ra, rb)), None)
      if hit:
        overlapping.append({"path": path, "a_lines": list(ra), "b_lines": list(hit)})
        break

  union = set(ia["files"]) | set(ib["files"])
  file_sim = sum(idf[p] for p in shared) / (sum(idf[p] for p in union) or 1.0)
  title_sim = cosine(titles[a], titles[b])

  # How much of each PR's change overlaps the other, weighted by file rarity.
  # Rewriting the same original lines counts fully; editing nearby lines counts
  # less, because line numbers drift between merge bases. Taking the geometric
  # mean of both sides stops a 200-file PR that shares one line with a small PR
  # from linking to it: real duplicates overlap heavily from both directions.
  weight = {s["path"]: 1.0 for s in same_lines}
  for h in overlapping:
    weight.setdefault(h["path"], 0.7)
  overlap = sum(idf[p] * w for p, w in weight.items())
  cover_a = overlap / sum(idf[p] for p in ia["files"])
  cover_b = overlap / sum(idf[p] for p in ib["files"])
  code_overlap = math.sqrt(cover_a * cover_b)

  score = 0.6 * code_overlap + 0.2 * file_sim + 0.2 * title_sim
  linked = (code_overlap >= 0.3 and score >= 0.45) or (file_sim >= 0.8 and title_sim >= 0.35)
  # Two PRs that both say they close the same issue are competing fixes even
  # when they edit different files.
  same_issues = sorted(closes.get(a, set()) & closes.get(b, set()))
  if same_issues:
    linked, score = True, max(score, 0.75)
  if not linked:
    return None
  return {"a": a, "b": b, "score": round(score, 3), "evidence": {
    "same_changed_lines": same_lines,
    "overlapping_hunks": overlapping,
    "same_closing_issues": same_issues,
    "shared_files": shared,
    "code_overlap": round(code_overlap, 3),
    "file_similarity": round(file_sim, 3),
    "title_similarity": round(title_sim, 3),
  }}


def closing_issues(pr, repo):
  owner, name = repo.split("/")
  return {i["number"] for i in pr.get("closingIssuesReferences") or []
          if i["repository"]["owner"]["login"] == owner and i["repository"]["name"] == name}


def build_edges(prs, infos, repo):
  analyzed = {n: i for n, i in infos.items() if i["error"] is None and i["files"]}
  df = collections.Counter(p for i in analyzed.values() for p in i["files"])
  n = max(len(analyzed), 1)
  idf = {p: math.log(1 + n / c) * (SECONDARY_WEIGHT if is_secondary(p) else 1.0) for p, c in df.items()}
  titles = title_vectors([p for p in prs if p["number"] in analyzed])

  by_path = collections.defaultdict(list)
  for number, info in analyzed.items():
    for path in info["files"]:
      by_path[path].append(number)
  pairs = set()
  for members in by_path.values():
    members.sort()
    for i, a in enumerate(members):
      for b in members[i + 1:]:
        pairs.add((a, b))
  closes = {p["number"]: closing_issues(p, repo) for p in prs if p["number"] in analyzed}
  by_issue = collections.defaultdict(list)
  for number, issues in closes.items():
    for issue in issues:
      by_issue[issue].append(number)
  for members in by_issue.values():
    members.sort()
    pairs.update((a, b) for i, a in enumerate(members) for b in members[i + 1:])
  print(f"comparing {len(pairs)} PR pairs that share a file or a closing issue", file=sys.stderr)

  edges = []
  for a, b in sorted(pairs):
    edge = compare(a, b, analyzed[a], analyzed[b], idf, titles, closes)
    if edge:
      edges.append(edge)
  return edges, df


def components(nodes, edges):
  parent = {n: n for n in nodes}

  def find(x):
    while parent[x] != x:
      parent[x] = parent[parent[x]]
      x = parent[x]
    return x

  for e in edges:
    parent[find(e["a"])] = find(e["b"])
  groups = collections.defaultdict(set)
  for n in nodes:
    groups[find(n)].add(n)
  return list(groups.values())


def cluster(edges):
  """Connected components. An oversized component is re-split by raising the
  minimum edge score until its pieces fit; PRs left with no edge drop out."""
  result, pending = [], [(edges, 0.0)]
  while pending:
    subset, floor = pending.pop()
    nodes = {e["a"] for e in subset} | {e["b"] for e in subset}
    for group in components(nodes, subset):
      inner = [e for e in subset if e["a"] in group and e["b"] in group]
      if len(group) <= MAX_CLUSTER or floor >= 1.0:
        result.append((group, inner))
      else:
        pending.append(([e for e in inner if e["score"] >= floor + 0.05], floor + 0.05))
  return result


# ---------------------------------------------------------------- output

def build(args):
  out = ROOT / args.out
  cache = ROOT / "cache"
  cache.mkdir(exist_ok=True)
  out.mkdir(exist_ok=True)

  prs = fetch_metadata(args.repo)
  print(f"{len(prs)} open PRs", file=sys.stderr)
  base_shas = sync_mirror(args.repo, cache / "mirror.git", prs)

  infos = {}
  with cf.ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
    for number, info in pool.map(lambda p: analyze_pr(cache / "mirror.git", p, base_shas), prs):
      infos[number] = info

  edges, file_df = build_edges(prs, infos, args.repo)
  groups = cluster(edges)

  meta = {p["number"]: p for p in prs}
  clusters = []
  for group, inner in groups:
    members = sorted(group)
    path_counts = collections.Counter(path for e in inner for path in e["evidence"]["shared_files"])
    clusters.append({
      "prs": members,
      "size": len(members),
      "max_score": max(e["score"] for e in inner),
      "hot_paths": [p for p, _ in path_counts.most_common(5)],
      "edges": sorted(inner, key=lambda e: -e["score"]),
    })

  now = datetime.datetime.now(datetime.timezone.utc)
  title_df = collections.Counter(w for p in prs for w in set(title_tokens(p["title"])))
  for c in clusters:
    c["summary"] = summarize_cluster(c, meta, title_df, len(prs), now, args.repo)
  # Triage queue order: where one session of testing retires the most PRs.
  clusters.sort(key=lambda c: (-c["summary"]["priority"], c["prs"][0]))
  for i, c in enumerate(clusters, 1):
    c["id"] = f"C{i:04d}"
    # IDs are renumbered every build; agent reviews are filed under the
    # cluster's oldest PR so they survive rebuilds.
    c["review_key"] = f"pr-{c['prs'][0]}"

  cluster_of = {n: c["id"] for c in clusters for n in c["prs"]}
  pr_records = {}
  for number, p in sorted(meta.items()):
    info = infos[number]
    pr_records[str(number)] = {
      "number": number,
      "title": p["title"],
      "url": p["url"],
      "author": (p.get("author") or {}).get("login"),
      "created_at": p["createdAt"],
      "updated_at": p["updatedAt"],
      "draft": p["isDraft"],
      "base": p["baseRefName"],
      "head_sha": p["headRefOid"],
      "merge_base": info["merge_base"],
      # GitHub's own answer; UNKNOWN (not computed yet) becomes null.
      "merges_cleanly": {"MERGEABLE": True, "CONFLICTING": False}.get(p.get("mergeable")),
      "labels": [l["name"] for l in p["labels"]],
      "review_decision": p.get("reviewDecision") or None,
      "additions": info["additions"],
      "deletions": info["deletions"],
      "large_diff_not_line_analyzed": info["large"],
      "analysis_error": info["error"],
      "files": sorted(info["files"]),
      "closes_issues": sorted(closing_issues(p, args.repo)),
      "cluster": cluster_of.get(number),
    }

  report = {
    "schema_version": SCHEMA_VERSION,
    "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "repo": args.repo,
    "base_branches": base_shas,
    "params": {"large_diff_lines": LARGE_DIFF_LINES, "hunk_tolerance": HUNK_TOLERANCE, "max_cluster": MAX_CLUSTER},
    "caveats": [
      "A cluster means these PRs change the same code. It does not mean they are duplicates or that any of them works.",
      "Nothing here says which PR to keep. Test the PRs (e.g. ./test/cli on each branch) before recommending one.",
      "Data is a snapshot: re-run build if generated_at is old, and confirm PR state on GitHub before acting.",
      "PRs flagged large_diff_not_line_analyzed are only compared by files and title.",
    ],
    "stats": {
      "open_prs": len(prs),
      "analyzed": sum(1 for i in infos.values() if i["error"] is None),
      "clustered_prs": len(cluster_of),
      "clusters": len(clusters),
      "hottest_files": [{"path": p, "open_prs": c} for p, c in file_df.most_common(10)],
    },
    "overview": summarize_backlog(clusters, meta, now),
    "clusters": clusters,
    "prs": pr_records,
  }
  (out / "clusters.json").write_text(json.dumps(report, indent=1))
  (out / "summary.json").write_text(json.dumps(render_summary(report), indent=1))
  (out / "clusters.md").write_text(render_markdown(report))
  (out / "index.html").write_text(render_html(report))
  s = report["stats"]
  print(f"{s['clustered_prs']} of {s['open_prs']} PRs fall into {s['clusters']} clusters -> {out}/", file=sys.stderr)


STALE_DAYS = 30


def age_days(stamp, now):
  return (now - datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))).days


def summarize_cluster(c, meta, title_df, total, now, repo):
  """Plain facts a triager can scan in five seconds. Deliberately no verdicts."""
  members = [meta[n] for n in c["prs"]]
  avg = sum(e["score"] for e in c["edges"]) / len(c["edges"])
  kind = "likely-duplicates" if avg >= 0.75 else "overlapping" if avg >= 0.55 else "related"

  # Words shared by several titles in this cluster but rare across all PRs.
  local = collections.Counter(w for p in members for w in set(title_tokens(p["title"])))
  need = max(2, math.ceil(len(members) * 0.3))
  topic = sorted((w for w, k in local.items() if k >= need), key=lambda w: -local[w] * math.log(total / title_df[w]))[:4]

  def having(pred):
    return [p["number"] for p in members if pred(p)]

  verified = having(lambda p: any(l["name"] in ("verified", "ready") for l in p["labels"]))
  approved = having(lambda p: p.get("reviewDecision") == "APPROVED")
  conflicting = having(lambda p: p.get("mergeable") == "CONFLICTING")
  drafts = having(lambda p: p["isDraft"])
  stale = having(lambda p: age_days(p["updatedAt"], now) >= STALE_DAYS)
  opened = sorted(p["createdAt"][:10] for p in members)
  closes = {p["number"]: closing_issues(p, repo) for p in members}
  issues = sorted(set().union(*closes.values()))
  most_issues = max(members, key=lambda p: (len(closes[p["number"]]), -p["number"]))["number"] if issues else None

  label = {"likely-duplicates": "Likely duplicates", "overlapping": "Overlapping", "related": "Related"}[kind]
  about = f"about “{', '.join(topic)}” " if topic else ""
  where = c["hot_paths"][0] if c["hot_paths"] else "the same files"
  parts = [f"{label}: {len(members)} PRs {about}changing {where}."]
  if verified:
    parts.append(f"Labeled verified/ready: {', '.join(f'#{n}' for n in verified)}.")
  else:
    parts.append("None labeled verified yet.")
  if approved:
    parts.append(f"Approved: {', '.join(f'#{n}' for n in approved)}.")
  if conflicting:
    parts.append(f"Conflicting: {', '.join(f'#{n}' for n in conflicting)}.")
  if most_issues:
    parts.append(f"#{most_issues} says it closes the most issues ({len(closes[most_issues])} of {len(issues)}).")

  return {
    "headline": " ".join(parts),
    "kind": kind,
    "topic_words": topic,
    "avg_score": round(avg, 3),
    # PRs this cluster could retire if triage settles on one, discounted by how
    # likely they are to really overlap.
    "priority": round((len(members) - 1) * avg, 3),
    "authors": len({(p.get("author") or {}).get("login") for p in members}),
    "opened_first": opened[0],
    "opened_last": opened[-1],
    "verified_or_ready": verified,
    "approved": approved,
    "conflicting": conflicting,
    "drafts": drafts,
    f"stale_{STALE_DAYS}d": stale,
    "issues": issues,
    "most_issues_pr": most_issues,
  }


def summarize_backlog(clusters, meta, now):
  kinds = collections.Counter(c["summary"]["kind"] for c in clusters)
  dup_prs = sum(c["size"] - 1 for c in clusters if c["summary"]["kind"] == "likely-duplicates")
  untested = [c["id"] for c in clusters if not c["summary"]["verified_or_ready"]]
  conflicting = sum(1 for p in meta.values() if p.get("mergeable") == "CONFLICTING")
  stale = sum(1 for p in meta.values() if age_days(p["updatedAt"], now) >= STALE_DAYS)
  lines = [
    f"{sum(c['size'] for c in clusters)} of {len(meta)} open PRs overlap with another open PR, in {len(clusters)} clusters.",
    f"{kinds['likely-duplicates']} clusters look like duplicates; settling each on one PR would retire up to {dup_prs} PRs.",
    f"{len(untested)} clusters have no PR labeled verified or ready.",
    f"{conflicting} open PRs conflict with their base branch; {stale} have not been updated in {STALE_DAYS}+ days.",
  ]
  return {
    "lines": lines,
    "cluster_kinds": dict(kinds),
    "duplicate_prs_retirable": dup_prs,
    "clusters_without_verified": len(untested),
    "conflicting_prs": conflicting,
    f"stale_{STALE_DAYS}d_prs": stale,
    "top_clusters": [{"id": c["id"], "prs": c["prs"], "headline": c["summary"]["headline"]} for c in clusters[:10]],
  }


def describe_edge(e):
  ev = e["evidence"]
  parts = []
  for s in ev["same_changed_lines"]:
    parts.append(f"both change {s['count']} identical line(s) in `{s['path']}` (e.g. `{s['sample']}`)")
  for h in ev["overlapping_hunks"]:
    if not any(s["path"] == h["path"] for s in ev["same_changed_lines"]):
      parts.append(f"edit nearby lines of `{h['path']}` ({h['a_lines'][0]}-{h['a_lines'][1]} vs {h['b_lines'][0]}-{h['b_lines'][1]})")
  if ev.get("same_closing_issues"):
    parts.append(f"both say they close {', '.join(f'#{n}' for n in ev['same_closing_issues'])}")
  if not parts:
    parts.append(f"same files ({', '.join('`' + p + '`' for p in ev['shared_files'][:4])})")
  parts.append(f"code overlap {ev['code_overlap']}, file similarity {ev['file_similarity']}, title similarity {ev['title_similarity']}")
  return "; ".join(parts)


def render_summary(report):
  """The compact file the Omarchy plugin reads: no raw edge evidence."""
  prs = report["prs"]
  keep = ("number", "title", "url", "author", "created_at", "draft", "merges_cleanly", "review_decision", "labels", "closes_issues")
  return {
    "schema_version": SCHEMA_VERSION,
    "generated_at": report["generated_at"],
    "repo": report["repo"],
    "triage_dir": str(ROOT),
    "overview": report["overview"],
    "caveats": report["caveats"],
    "clusters": [{
      "id": c["id"],
      "review_key": c["review_key"],
      "prs": c["prs"],
      "size": c["size"],
      "hot_paths": c["hot_paths"],
      **c["summary"],
      # Backticks mark code for Markdown/HTML; the plugin shows plain text.
      "why": [describe_edge(e).replace("`", "") for e in c["edges"][:3]],
    } for c in report["clusters"]],
    "prs": {k: {f: v[f] for f in keep} for k, v in prs.items() if v["cluster"]},
  }


def render_markdown(report):
  prs = report["prs"]
  lines = [
    f"# Open PR clusters for {report['repo']}",
    "",
    f"Generated {report['generated_at']}. {report['stats']['clustered_prs']} of {report['stats']['open_prs']} open PRs fall into {report['stats']['clusters']} clusters.",
    "",
    *[f"> {c}" for c in report["caveats"]],
    "",
    "## Overview",
    "",
    *[f"- {line}" for line in report["overview"]["lines"]],
    "",
    "Top of the triage queue (clusters where settling on one PR retires the most PRs):",
    "",
    *[f"1. **{t['id']}** ({', '.join(f'#{n}' for n in t['prs'])}): {t['headline']}" for t in report["overview"]["top_clusters"]],
    "",
  ]
  for c in report["clusters"]:
    lines += [f"## {c['id']}: {c['size']} PRs", "", f"**{c['summary']['headline']}**", "", f"Files: {', '.join('`' + p + '`' for p in c['hot_paths'])}", "",
              "| PR | Title | Author | Opened | Merges cleanly | Review | Labels |", "|---|---|---|---|---|---|---|"]
    for n in c["prs"]:
      p = prs[str(n)]
      title = p["title"].replace("|", "\\|")
      clean = {True: "yes", False: "conflicts", None: "?"}[p["merges_cleanly"]]
      lines.append(f"| [#{n}]({p['url']}) | {title}{' (draft)' if p['draft'] else ''} | @{p['author']} | {p['created_at'][:10]} | {clean} | {p['review_decision'] or ''} | {', '.join(p['labels'])} |")
    lines += ["", "Why they are grouped:", ""]
    lines += [f"- #{e['a']} ↔ #{e['b']} (score {e['score']}): {describe_edge(e)}" for e in c["edges"]]
    lines.append("")
  return "\n".join(lines)


def render_html(report):
  data = {
    "generated_at": report["generated_at"],
    "repo": report["repo"],
    "stats": report["stats"],
    "caveats": report["caveats"],
    "overview": report["overview"],
    "triage_dir": str(ROOT),
    "clusters": [{**c, "edges": [{**e, "why": describe_edge(e)} for e in c["edges"]]} for c in report["clusters"]],
    "prs": {k: {f: v[f] for f in ("number", "title", "url", "author", "created_at", "draft", "merges_cleanly", "review_decision", "labels", "cluster")}
            for k, v in report["prs"].items() if v["cluster"]},
  }
  payload = json.dumps(data).replace("</", "<\\/")
  return (HTML_TEMPLATE.replace("__DATA__", payload).replace("__TITLE__", html.escape(report["repo"]))
          .replace("__THEME__", theme_css()).replace("__FONT__", json.dumps(omarchy_font())))


# The page borrows the active Omarchy theme so it sits next to the desktop
# without clashing. Fallbacks keep it readable off Omarchy.
THEME_FILE = Path.home() / ".local/state/omarchy/current/theme/colors.toml"
THEME_VARS = {  # css var: (colors.toml keys in preference order, fallback)
  "bg": (["background"], "#111418"),
  "panel": (["lighter_background", "dark_background"], "#1a1f25"),
  "line": (["selection", "muted"], "#2b323b"),
  "sel": (["selection"], "#243049"),
  "fg": (["foreground"], "#d8dee6"),
  "dim": (["dark_foreground", "muted"], "#8b95a3"),
  "accent": (["accent", "blue"], "#7aa2f7"),
  "ok": (["green"], "#9ece6a"),
  "bad": (["red"], "#f7768e"),
  "warn": (["yellow"], "#e0af68"),
}


def theme_css():
  try:
    colors = tomllib.loads(THEME_FILE.read_text())
  except (OSError, tomllib.TOMLDecodeError):
    colors = {}
  pick = lambda keys, fallback: next((colors[k] for k in keys if isinstance(colors.get(k), str)), fallback)
  return ";".join(f"--{var}:{pick(keys, fallback)}" for var, (keys, fallback) in THEME_VARS.items())


def omarchy_font():
  proc = subprocess.run(["omarchy-font-current"], capture_output=True, text=True) if shutil.which("omarchy-font-current") else None
  return proc.stdout.strip() if proc and proc.returncode == 0 and proc.stdout.strip() else "monospace"


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>PR clusters: __TITLE__</title>
<style>
:root{__THEME__}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 __FONT__,monospace;background:var(--bg);color:var(--fg);display:grid;grid-template-columns:360px 1fr;height:100vh}
aside{border-right:1px solid var(--line);display:flex;flex-direction:column;min-height:0}
header{padding:12px;border-bottom:1px solid var(--line)}header h1{font-size:15px;margin:0 0 4px}header p{margin:0;color:var(--dim);font-size:12px}
input{width:100%;margin-top:8px;padding:7px 9px;background:var(--panel);border:1px solid var(--line);color:var(--fg);border-radius:6px}
#list{overflow:auto;flex:1}.item{padding:8px 12px;border-bottom:1px solid var(--line);cursor:pointer}.item:hover,.item.sel{background:var(--panel)}
.item b{color:var(--accent)}.item small{display:block;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kind{font-size:11px;padding:1px 6px;border-radius:9px;border:1px solid currentColor}.likely-duplicates{color:var(--bad)}.overlapping{color:var(--warn)}.related{color:var(--dim)}
.headline{font-size:15px;background:var(--panel);border-left:3px solid var(--accent);padding:10px 12px;border-radius:4px}
.stats li{font-size:15px;margin:8px 0}.top li{cursor:pointer;margin:8px 0}.top li:hover{color:var(--accent)}
.agent{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:12px 0}.agent code{user-select:all}
button{font:inherit;background:var(--panel);color:var(--accent);border:1px solid var(--accent);border-radius:6px;padding:6px 12px;cursor:pointer}button:hover{background:var(--sel)}
main{overflow:auto;padding:16px 20px}.caveat{color:var(--warn);font-size:12px;margin:0 0 12px}
svg{background:var(--panel);border-radius:8px;width:100%;max-width:760px;height:380px;display:block}
table{border-collapse:collapse;width:100%;margin:14px 0}td,th{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}th{color:var(--dim);font-weight:500}
a{color:var(--accent)}.ok{color:var(--ok)}.bad{color:var(--bad)}.dim{color:var(--dim)}code{background:var(--panel);padding:1px 4px;border-radius:4px;font-size:12px}
li{margin:4px 0}li.hl{background:var(--sel);border-radius:4px}
</style></head><body>
<aside><header><h1>PR clusters · __TITLE__</h1><p id="summary"></p>
<input id="q" placeholder="Filter: PR number, author, file, words…" autofocus></header><div id="list"></div></aside>
<main id="main"></main>
<script>
const D=__DATA__;
const $=s=>document.querySelector(s), esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
$("#summary").textContent=`${D.stats.clustered_prs} of ${D.stats.open_prs} open PRs in ${D.stats.clusters} clusters · ${D.generated_at}`;
const KIND={"likely-duplicates":"likely duplicates","overlapping":"overlapping","related":"related"};
const badge=c=>`<span class="kind ${c.summary.kind}">${KIND[c.summary.kind]}</span>`;
function text(c){return [c.id,c.summary.kind,...c.summary.topic_words,...c.hot_paths,...c.prs.map(n=>{const p=D.prs[n];return `#${n} ${n} ${p.title} ${p.author}`})].join(" ").toLowerCase()}
const idx=D.clusters.map(c=>[c,text(c)]);
function list(){const q=$("#q").value.toLowerCase().trim().split(/\s+/).filter(Boolean);
 $("#list").innerHTML=(q.length?"":`<div class="item" data-id="overview"><b>Overview</b><small>Backlog summary and the top of the triage queue</small></div>`)+
 idx.filter(([c,t])=>q.every(w=>t.includes(w))).slice(0,400).map(([c])=>
 `<div class="item" data-id="${c.id}"><b>${c.id}</b> · ${c.size} PRs ${badge(c)}<small>${esc(c.summary.topic_words.join(", ")||D.prs[c.prs[0]].title)}</small><small>${esc(c.hot_paths.join(", "))}</small></div>`).join("");
 select(location.hash.slice(1)||"overview");}
function select(id){document.querySelectorAll(".item").forEach(e=>e.classList.toggle("sel",e.dataset.id===id))}
function overview(){select("overview");location.hash="overview";const o=D.overview;
 $("#main").innerHTML=`<h2>Triage overview</h2><p class="dim">Snapshot ${D.generated_at}</p><ul class="stats">${o.lines.map(l=>`<li>${esc(l)}</li>`).join("")}</ul>
 <h3>Top of the queue</h3><p class="dim">Clusters where settling on one PR retires the most open PRs.</p>
 <ol class="top">${o.top_clusters.map(t=>`<li data-id="${t.id}"><b>${t.id}</b> ${esc(t.headline)}</li>`).join("")}</ol>
 <h3>Before acting</h3>${D.caveats.map(t=>`<p class="caveat">⚠ ${esc(t)}</p>`).join("")}`;
 document.querySelectorAll(".top li").forEach(li=>li.onclick=()=>show(li.dataset.id));}
const shq=s=>"'"+String(s).replace(/'/g,"'\\''")+"'";
const agentCmd=c=>`${shq(D.triage_dir+"/pr_clusters.py")} agent ${c.prs[0]}`;
async function copyText(t){try{await navigator.clipboard.writeText(t);return true}catch{const a=document.createElement("textarea");a.value=t;document.body.append(a);a.select();const ok=document.execCommand("copy");a.remove();return ok}}
document.addEventListener("click",async e=>{const b=e.target.closest("button[data-cmd]");if(!b)return;
 const ok=await copyText(b.dataset.cmd);$("#copied").textContent=ok?"Copied. Paste it in a terminal.":"Copy failed; select the command and copy it.";});
function status(p){return p.merges_cleanly===true?'<span class="ok">merges cleanly</span>':p.merges_cleanly===false?'<span class="bad">conflicts</span>':'<span class="dim">?</span>'}
function show(id){if(id==="overview")return overview();const c=D.clusters.find(x=>x.id===id);if(!c)return overview();
 select(id);location.hash=id;const s=c.summary;
 const W=760,H=380,R=Math.min(150,40+c.size*14),pos={};
 c.prs.forEach((n,i)=>{const a=2*Math.PI*i/c.size-Math.PI/2;pos[n]=[W/2+R*Math.cos(a),H/2+R*Math.sin(a)]});
 const edges=c.edges.map((e,i)=>{const[a,b]=[pos[e.a],pos[e.b]];return `<line data-e="${i}" x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" style="stroke:var(--accent)" stroke-opacity="${0.25+e.score*0.7}" stroke-width="${1+e.score*6}"><title>#${e.a} ↔ #${e.b}: ${esc(e.why)}</title></line>`}).join("");
 const nodes=c.prs.map(n=>{const p=D.prs[n],[x,y]=pos[n],col=p.merges_cleanly===false?"var(--bad)":p.merges_cleanly?"var(--ok)":"var(--dim)";
  return `<a href="${p.url}" target="_blank"><circle cx="${x}" cy="${y}" r="20" style="fill:var(--bg);stroke:${col}" stroke-width="3"><title>${esc(p.title)}</title></circle><text x="${x}" y="${y+4}" style="fill:var(--fg)" font-size="11" text-anchor="middle">${n}</text></a>`}).join("");
 $("#main").innerHTML=`<h2>${c.id}: ${c.size} PRs ${badge(c)}</h2><p class="headline">${esc(s.headline)}</p>
 <p class="dim">${s.authors} authors · opened ${s.opened_first} → ${s.opened_last}${s.drafts.length?` · ${s.drafts.length} drafts`:""}${s.stale_30d.length?` · ${s.stale_30d.length} not updated in 30+ days`:""} · Files: ${c.hot_paths.map(p=>`<code>${esc(p)}</code>`).join(" ")}</p>
 <div class="agent"><button data-cmd="${esc(agentCmd(c))}">Send an agent ↗ copy command</button><code>${esc(agentCmd(c))}</code><span class="dim" id="copied"></span></div>
 <p class="dim">Paste it in a terminal: your default agent reviews this cluster, picks a core PR, and notifies you when a human is needed. (A web page can't launch it directly; that would let any site start an auto-approved agent.) Clusters are leads, not verdicts.</p>
 <svg viewBox="0 0 ${W} ${H}">${edges}${nodes}</svg><p class="dim">Ring colour: green merges cleanly, red conflicts. Thicker line = stronger overlap; hover a line for the evidence.</p>
 <table><tr><th>PR</th><th>Title</th><th>Author</th><th>Opened</th><th>Base</th><th>Review</th><th>Labels</th></tr>
 ${c.prs.map(n=>{const p=D.prs[n];return `<tr><td><a href="${p.url}" target="_blank">#${n}</a></td><td>${esc(p.title)}${p.draft?' <span class="dim">(draft)</span>':""}</td><td>@${esc(p.author)}</td><td>${p.created_at.slice(0,10)}</td><td>${status(p)}</td><td>${p.review_decision||""}</td><td>${esc(p.labels.join(", "))}</td></tr>`}).join("")}</table>
 <h3>Why they are grouped</h3><ul>${c.edges.map((e,i)=>`<li data-e="${i}"><a href="${D.prs[e.a].url}" target="_blank">#${e.a}</a> ↔ <a href="${D.prs[e.b].url}" target="_blank">#${e.b}</a> <span class="dim">(score ${e.score})</span>: ${esc(e.why).replace(/`([^`]+)`/g,"<code>$1</code>")}</li>`).join("")}</ul>`;
 document.querySelectorAll("line").forEach(l=>l.onmouseenter=()=>document.querySelectorAll("li[data-e]").forEach(li=>li.classList.toggle("hl",li.dataset.e===l.dataset.e)));}
$("#q").oninput=list;$("#list").onclick=e=>{const it=e.target.closest(".item");if(it)show(it.dataset.id)};
list();show(location.hash.slice(1)||"overview");
</script></body></html>
"""


def find_cluster(args):
  report = json.loads((ROOT / args.out / "clusters.json").read_text())
  pr = report["prs"].get(str(args.pr))
  if not pr:
    sys.exit(f"#{args.pr} was not an open PR when this report was generated ({report['generated_at']})")
  if not pr["cluster"]:
    sys.exit(f"#{args.pr} {pr['title']}\nNot clustered: no other open PR changes the same code (as of {report['generated_at']}).")
  return report, next(c for c in report["clusters"] if c["id"] == pr["cluster"])


def show(args):
  report, cluster = find_cluster(args)
  if args.json:
    print(json.dumps({"cluster": cluster, "prs": {n: report["prs"][str(n)] for n in cluster["prs"]}}, indent=1))
    return
  print(f"{cluster['id']} ({cluster['size']} PRs, snapshot {report['generated_at']})")
  print(f"  {cluster['summary']['headline']}")
  review = read_index()["reviews"].get(cluster["review_key"])
  if review:
    print(f"  Agent review: {review['status']}" + (f", core PR #{review['core_pr']} ({review['confidence']}): {review['review_md']}" if review["status"] == "done" else ""))
  for n in cluster["prs"]:
    p = report["prs"][str(n)]
    clean = {True: "merges cleanly", False: "CONFLICTS", None: "?"}[p["merges_cleanly"]]
    print(f"  #{n:<6} {p['created_at'][:10]}  @{p['author']:<18} {clean:<15} {p['title']}")
  print("why:")
  for e in cluster["edges"]:
    print(f"  #{e['a']} <-> #{e['b']} ({e['score']}): {describe_edge(e)}")


# ---------------------------------------------------------------- agent reviews

REVIEWS = ROOT / "reviews"
INDEX = REVIEWS / "index.json"
REVIEW_FIELDS = {"prs": list, "reviewed_at": str, "confidence": str, "summary": str, "ranking": list, "human_actions": list}


def read_index():
  try:
    return json.loads(INDEX.read_text())
  except (OSError, json.JSONDecodeError):
    return {"reviews": {}}


def update_index(key, **fields):
  index = read_index()
  index["reviews"].setdefault(key, {"key": key}).update(fields)
  REVIEWS.mkdir(exist_ok=True)
  tmp = INDEX.with_suffix(".tmp")
  tmp.write_text(json.dumps(index, indent=1))
  tmp.replace(INDEX)  # atomic, so the plugin never reads a half-written file


def utc_now():
  return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def agent(args):
  report, cluster = find_cluster(args)
  key = cluster["review_key"]
  review_dir, work_dir = REVIEWS / key, REVIEWS / key / "work"
  work_dir.mkdir(parents=True, exist_ok=True)
  for old in ("review.json", "review.md"):
    (review_dir / old).unlink(missing_ok=True)

  cluster_json = review_dir / "cluster.json"
  cluster_json.write_text(json.dumps({"generated_at": report["generated_at"], "caveats": report["caveats"], "cluster": cluster,
                                      "prs": {n: report["prs"][str(n)] for n in cluster["prs"]}}, indent=1))
  finish_cmd = " ".join(shlex.quote(a) for a in (sys.executable, str(ROOT / "pr_clusters.py"), "finish", key))
  brief = (ROOT / "prompts/cluster-review.md").read_text().format(
    repo=report["repo"], key=key, cluster_json=cluster_json, review_dir=review_dir, work_dir=work_dir,
    mirror=ROOT / "cache/mirror.git", finish_cmd=finish_cmd,
    pr_list=", ".join(f"#{n} ({report['prs'][str(n)]['url']})" for n in cluster["prs"]))
  (review_dir / "brief.md").write_text(brief)
  prompt = f"Read {review_dir / 'brief.md'} and follow it exactly. It is your only source of instructions."
  if args.print_prompt:
    print(prompt)
    return

  # omarchy agent prompt execs into the terminal that hosts the agent, so it
  # lives as long as the agent does. Detach it into its own session and only
  # watch the first seconds for a launch failure (no default agent, missing
  # binary); waiting longer would tie the agent's life to this command.
  launch_log = review_dir / "launch.log"
  with launch_log.open("w") as log:
    launch = subprocess.Popen(["bash", "-c", 'cd "$1" && exec omarchy agent prompt "$2"', "_", str(work_dir), prompt],
                              stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
  try:
    failed = launch.wait(timeout=3) != 0
  except subprocess.TimeoutExpired:
    failed = False  # still running: the agent's terminal is up
  if failed:
    sys.exit(f"could not launch the agent: {launch_log.read_text().strip()}")
  update_index(key, status="running", prs=cluster["prs"], started_at=utc_now(), review_md=str(review_dir / "review.md"))
  print(f"Agent started on {key} ({', '.join(f'#{n}' for n in cluster['prs'])}). It will notify you when a human is needed.")


def finish(args):
  review_dir = REVIEWS / args.key
  try:
    review = json.loads((review_dir / "review.json").read_text())
  except (OSError, json.JSONDecodeError) as e:
    sys.exit(f"review.json unreadable: {e}")
  problems = [f"{k} must be a {t.__name__}" for k, t in REVIEW_FIELDS.items() if not isinstance(review.get(k), t)]
  if review.get("confidence") not in ("high", "medium", "low"):
    problems.append("confidence must be high, medium or low")
  core = review.get("core_pr")
  if core is not None and core not in review.get("prs", []):
    problems.append("core_pr must be null or one of prs")
  if not (review_dir / "review.md").is_file():
    problems.append("review.md is missing")
  if problems:
    sys.exit("review.json is invalid:\n- " + "\n- ".join(problems))

  actions = len(review["human_actions"])
  update_index(args.key, status="done", prs=review["prs"], reviewed_at=review["reviewed_at"], core_pr=core,
               confidence=review["confidence"], summary=review["summary"], actions=actions,
               review_md=str(review_dir / "review.md"))
  # The worktrees and clone were only for this run; the verdict is in review.*.
  shutil.rmtree(review_dir / "work", ignore_errors=True)

  verdict = f"Core PR #{core}" if core else "No single core PR"
  subprocess.run(["omarchy-notification-send", "-u", "normal", "PR triage: human needed",
                  f"{args.key}: {verdict} ({review['confidence']} confidence). {actions} action(s) waiting.",
                  "--exec", "xdg-open", str(review_dir / "review.md")], check=False)
  print(f"Recorded {args.key}: {verdict}. Human notified.")


def main():
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--repo", default="omacom/omarchy")
  parser.add_argument("--out", default="out")
  sub = parser.add_subparsers(dest="cmd", required=True)
  sub.add_parser("build", help="fetch open PRs and write out/clusters.{json,md} and out/index.html")
  s = sub.add_parser("show", help="print the cluster containing a PR")
  s.add_argument("pr", type=int)
  s.add_argument("--json", action="store_true", help="machine-readable output for agents")
  a = sub.add_parser("agent", help="launch your default coding agent to review the cluster containing a PR")
  a.add_argument("pr", type=int)
  a.add_argument("--print-prompt", action="store_true", help="write the brief and print the prompt without launching")
  f = sub.add_parser("finish", help="(run by the agent) validate a review, record it, and notify the human")
  f.add_argument("key")
  args = parser.parse_args()
  # Exit quietly when piped into `head`, which agents do constantly.
  signal.signal(signal.SIGPIPE, signal.SIG_DFL)
  {"build": build, "show": show, "agent": agent, "finish": finish}[args.cmd](args)


if __name__ == "__main__":
  main()
