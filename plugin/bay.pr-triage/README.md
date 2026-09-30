# PR Triage

An Omarchy shell bar widget for a volunteer PR-triage team. A bar icon opens an
anchored popup that lists clusters of overlapping open pull requests (in triage
order), shows why they were grouped, and lets you ask a coding agent to review a
cluster. It only *reads* the output of `pr_clusters.py`; it does not talk to
GitHub itself.

- **Left click** the icon: toggle the popup. **Middle click**: rebuild the snapshot. **Right click**: open the full HTML view.
- Popup: overview, search (PR number, author, words, file path), cluster list with kind badge and review status chip; select a cluster for its PRs, agent review, and actions.
- Keys: type to search, `Up`/`Down` move, `Enter` opens a cluster, `Esc` clears the search, closes the popup, or goes back from a detail view. In detail view: `Up`/`Down` scroll, `Left`/`h` back, `b` open the HTML view, `o` open the review.
- The bar badge shows how many clusters have a finished, current agent review (an ellipsis while a rebuild runs).

## Install / enable / remove

```bash
# from a clone of this repo, cloned to ~/Developer/omarchy-triage
cp -r plugin/bay.pr-triage ~/.config/omarchy/plugins/
omarchy-plugin-validate ~/.config/omarchy/plugins/bay.pr-triage
omarchy-shell shell rescanPlugins
omarchy plugin enable bay.pr-triage --section right

omarchy-shell shell summon bay.pr-triage    # open the popup
omarchy-shell shell hide bay.pr-triage      # close it

omarchy plugin disable bay.pr-triage        # take it off the bar
omarchy plugin remove bay.pr-triage         # uninstall
```

## IPC

`omarchy-shell bay.pr-triage <open|close|toggle|rebuild|back>`, `search <text>`, and `select <cluster id>` (for keybindings and scripted checks).

## Configuration

One bar-widget setting, `triageDir` (default `~/Developer/omarchy-triage`),
the directory holding `pr_clusters.py`. Set it on the widget's entry in
`~/.config/omarchy/shell.json` (`{ "id": "bay.pr-triage", "triageDir": "~/triage" }`);
a leading `~` is expanded.

## Data contract

Files read (and watched for changes) under `triageDir`:

- `out/summary.json` — clusters, PR metadata, overview lines, caveats.
- `reviews/index.json` — agent review status per `review_key`. A review is
  *stale* if its PR set differs from the cluster's, and a `running` review older
  than 6 hours is shown as *abandoned*.

Commands run:

| Action | Command |
| --- | --- |
| Rebuild | `pr_clusters.py build` (~60 s, in-panel busy state, stderr tail shown on failure) |
| Ask agent | `pr_clusters.py agent <first PR of the cluster>` |
| Open PR / review / HTML | `xdg-open` |

The producer script and the JSON schema are documented in the triage repo itself
(`README.md` next to `pr_clusters.py`).

## Caveats

- **"Ask agent to review" launches a real, auto-approved coding agent** in a
  terminal (via `pr_clusters.py agent`). It runs immediately, with no confirmation
  step, and can act without asking you first. Only click it when you mean it.
- Clusters are leads, not verdicts: a cluster means the PRs touch the same code,
  not that they are duplicates or that any of them works. Test before recommending.
- The snapshot is only as fresh as the last rebuild; check the age in the header.
