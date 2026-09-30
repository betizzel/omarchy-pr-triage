.pragma library

// Pure helpers for the PR Triage panel: no QML types, no I/O.

var ABANDON_MS = 6 * 60 * 60 * 1000

function ageText(iso, nowMs) {
  var t = Date.parse(iso || "")
  if (isNaN(t)) return ""
  var s = Math.max(0, Math.round((nowMs - t) / 1000))
  if (s < 60) return "just now"
  var m = Math.round(s / 60)
  if (m < 60) return m + " min ago"
  var h = Math.round(m / 60)
  if (h < 48) return h + " h ago"
  return Math.round(h / 24) + " d ago"
}

function sameSet(a, b) {
  if (!a || !b || a.length !== b.length) return false
  var x = a.slice().sort(function(p, q) { return p - q })
  var y = b.slice().sort(function(p, q) { return p - q })
  for (var i = 0; i < x.length; i++) if (x[i] !== y[i]) return false
  return true
}

// state: none | running | abandoned | done | stale
function reviewStatus(cluster, review, nowMs) {
  if (!cluster || !review) return { state: "none", label: "no review" }
  if (review.status === "running") {
    var started = Date.parse(review.started_at || "")
    if (!isNaN(started) && nowMs - started > ABANDON_MS) return { state: "abandoned", label: "abandoned" }
    return { state: "running", label: "running" }
  }
  if (review.status === "done") {
    if (!sameSet(review.prs, cluster.prs)) return { state: "stale", label: "stale" }
    return { state: "done", label: review.core_pr ? "done: core #" + review.core_pr : "done" }
  }
  return { state: "none", label: "no review" }
}

function kindLabel(kind) {
  if (kind === "likely-duplicates") return "likely duplicates"
  return kind || ""
}

function findCluster(summary, id) {
  if (!summary || !id) return null
  var cs = summary.clusters || []
  for (var i = 0; i < cs.length; i++) if (cs[i].id === id) return cs[i]
  return null
}

// One lowercase haystack per cluster: id, PR numbers/titles/authors/labels,
// topic words, hot paths and the headline.
function buildIndex(summary) {
  var out = []
  if (!summary) return out
  var cs = summary.clusters || []
  var prs = summary.prs || {}
  for (var i = 0; i < cs.length; i++) {
    var c = cs[i]
    var parts = [c.id, c.review_key, c.kind, c.headline]
    parts = parts.concat(c.topic_words || [], c.hot_paths || [])
    for (var j = 0; j < (c.prs || []).length; j++) {
      var n = c.prs[j]
      var p = prs[String(n)]
      parts.push("#" + n, String(n))
      if (p) parts.push(p.title, p.author)
    }
    out.push({ cluster: c, hay: parts.join("\n").toLowerCase() })
  }
  return out
}

function filterIndex(index, query) {
  var q = String(query || "").trim().toLowerCase()
  var res = []
  var i
  if (!q) {
    for (i = 0; i < index.length; i++) res.push(index[i].cluster)
    return res
  }
  var tokens = q.split(/\s+/)
  for (i = 0; i < index.length; i++) {
    var ok = true
    for (var t = 0; t < tokens.length; t++) {
      var tok = tokens[t]
      if (!tok) continue
      if (index[i].hay.indexOf(tok) === -1) { ok = false; break }
    }
    if (ok) res.push(index[i].cluster)
  }
  return res
}

// Clusters with a finished, non-stale review.
function readyReviewCount(summary, reviews, nowMs) {
  if (!summary) return 0
  var n = 0
  var cs = summary.clusters || []
  for (var i = 0; i < cs.length; i++) {
    var r = reviews ? reviews[cs[i].review_key] : null
    if (reviewStatus(cs[i], r, nowMs).state === "done") n++
  }
  return n
}

function isVerified(pr) {
  var labels = pr && pr.labels ? pr.labels : []
  for (var i = 0; i < labels.length; i++) {
    var l = String(labels[i]).toLowerCase()
    if (l === "verified" || l === "ready") return true
  }
  return false
}

function tailLines(text, n) {
  var lines = String(text || "").trim().split("\n")
  return lines.slice(-n).join("\n")
}

function plural(n, word) {
  return n + " " + word + (n === 1 ? "" : "s")
}

function parseJson(text) {
  try { return JSON.parse(text) } catch (e) { return null }
}
