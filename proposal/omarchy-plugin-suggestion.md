# Suggestion: an Omarchy bar plugin for triage-o-mator

triage-o-mator is built with the Omarchy backlog in mind, and most people triaging that backlog run Omarchy. A small Omarchy shell plugin could put the triage state on the bar, so the work is one click away instead of a terminal session away. A working prototype exists: `bay.pr-triage`, a bar widget built for `pr_clusters.py`. Pointing it at a triage-o-mator install instead is mostly a change of data source.

## What it would show

- **A bar badge** with the number of `[human]` suggestions from `bin/next --json`: the things only a person can do, like confirming proposals or marking groups ready.
- **A popup** with four short lists:
  - **Next:** `bin/next --json`, split into human and agent steps.
  - **Leads:** candidate sets from `bin/cache candidates --compact --corpus <selected dataset>`, each with the signals that linked it. Changed lines come first.
  - **Confirmed:** the `confirmed_duplicates` from the same call, so a reviewer can see what is already settled.
  - **Attention:** watched PRs with new activity, from the existing offline attention reader.
- **Progress** from `bin/stats`: triaged and reviewed counts.
- **Colors and font** from the current Omarchy theme, like every other bar widget.

## What it would do

| Action | Runs |
| --- | --- |
| Open the TUI | `omarchy-launch-tui <install>/bin/triage-o-mator` |
| Ask an agent to compare a lead | `omarchy agent prompt` in the install, pointed at `prompts/review-candidate-set.md` and the group ID |
| Open a PR | `xdg-open` |
| Refresh | `bin/cache candidates` again (offline) |

## Rules it would keep

- **Scripts own managed data** (triage-o-mator invariant 2). The plugin only runs read-only scripts and opens things. It never writes a ledger, group, verdict or cache file, and it never approves anything.
- **Nothing from the plugin reaches GitHub.** Discovery and reads are offline. Downloads stay in the TUI, where a person chooses the scope and budget.
- **An agent launch is explicit.** One click on one lead, never a background job. The agent's output is an unreviewed proposal, like every other playbook.

## Lessons from the prototype

- `omarchy agent prompt` stays alive as long as the agent's terminal does. A caller that waits for it with a timeout kills the agent when the timeout fires. Start it detached and only watch the first seconds for a launch error.
- A web page can't safely launch an agent: a local endpoint would let any site start an auto-approved one. The bar plugin is part of the desktop, so it doesn't have that problem.
- Show how old the data is. The popup is only as current as the last acquisition.

## Where it would live

Either as `integrations/omarchy/` in triage-o-mator, next to the TUI it complements, or as a standalone Omarchy plugin that takes the install path as its one setting. The second keeps Omarchy-specific QML out of a tool that also serves other repositories.
