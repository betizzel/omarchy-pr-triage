import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui
import "Model.js" as Model

// PR Triage popup: cluster queue, cluster detail, agent-review status.
// Data comes from pr_clusters.py (see README.md for the file contract).
Panel {
  id: root
  moduleName: "bay.pr-triage"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  // ---- configuration ------------------------------------------------------
  readonly property string defaultTriageDir: "~/Developer/omarchy-triage"
  readonly property string triageDir: {
    var d = String(setting("triageDir", defaultTriageDir))
    if (d.indexOf("~/") === 0) d = Quickshell.env("HOME") + d.slice(1)
    return d.replace(/\/+$/, "")
  }
  readonly property string script: triageDir + "/pr_clusters.py"

  // ---- theme tokens -------------------------------------------------------
  readonly property color fg: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(fg, 1.4)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property color urgentTone: bar ? bar.urgent : Color.urgent
  // Theme tokens have no yellow or green, so derive them from the foreground.
  readonly property color warnTone: Qt.tint(fg, Qt.rgba(0.92, 0.72, 0.25, 0.85))
  readonly property color goodTone: Qt.tint(fg, Qt.rgba(0.45, 0.78, 0.48, 0.8))
  readonly property color cardFill: Util.alpha(fg, 0.06)

  // ---- state --------------------------------------------------------------
  property var summary: null
  property string summaryError: ""
  property var reviews: ({})
  property string lastReviewsText: "\u0000"
  property var searchIndex: []
  property var filtered: []
  property string query: ""
  property int listIndex: 0
  property string selectedId: ""
  property real now: Date.now()
  property string buildError: ""

  readonly property var selected: Model.findCluster(summary, selectedId)
  readonly property bool detailOpen: selected !== null
  readonly property bool building: buildProc.running
  readonly property int readyReviews: Model.readyReviewCount(summary, reviews, now)
  readonly property string snapshotAge: summary ? Model.ageText(summary.generated_at, now) : ""

  function statusFor(cluster) {
    return Model.reviewStatus(cluster, cluster ? reviews[cluster.review_key] : null, now)
  }

  function toneForKind(kind) {
    if (kind === "likely-duplicates") return urgentTone
    if (kind === "overlapping") return warnTone
    return dim
  }

  function toneForState(state) {
    if (state === "done") return goodTone
    if (state === "running") return Color.accent
    if (state === "stale") return warnTone
    if (state === "abandoned") return urgentTone
    return dim
  }

  // ---- lifecycle (same shape as the first-party popup panels) --------------
  function reloadAll() {
    now = Date.now()
    summaryFile.reload()
    indexFile.reload()
  }

  function open() {
    setCenterHoverRevealSuppressed(false)
    root.controller.show()
    reloadAll()
  }

  function openFromHotkey() {
    root.controller.show()
    reloadAll()
    // Set after showing: showing hands the popout coordinator over, whose
    // close of the previous panel clears the shared flag.
    Qt.callLater(function() {
      if (root.opened) setCenterHoverRevealSuppressed(true)
    })
  }

  function close() {
    setCenterHoverRevealSuppressed(false)
    root.controller.hide()
  }

  function toggle() {
    if (root.opened) root.close()
    else root.openFromHotkey()
  }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  function setCenterHoverRevealSuppressed(value) {
    if (root.bar && typeof root.bar.setCenterHoverRevealSuppressed === "function")
      root.bar.setCenterHoverRevealSuppressed(value)
    else if (root.bar && "centerHoverRevealSuppressed" in root.bar)
      root.bar.centerHoverRevealSuppressed = value
  }

  // ---- navigation ---------------------------------------------------------
  function refilter() {
    filtered = Model.filterIndex(searchIndex, query)
    listIndex = Math.max(0, Math.min(listIndex, filtered.length - 1))
  }

  onQueryChanged: {
    listIndex = 0
    refilter()
    clusterList.positionViewAtBeginning()
  }

  function openCluster(cluster) {
    if (!cluster) return
    selectedId = cluster.id
    detailFlick.contentY = 0
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function back() {
    selectedId = ""
    Qt.callLater(function() { searchField.forceActiveFocus() })
  }

  function setQuery(text) {
    searchField.text = text
  }

  function selectById(id) {
    var c = Model.findCluster(summary, id)
    if (c) openCluster(c)
  }

  function moveList(delta) {
    if (filtered.length === 0) return
    listIndex = Math.max(0, Math.min(filtered.length - 1, listIndex + delta))
    clusterList.positionViewAtIndex(listIndex, ListView.Contain)
  }

  // ---- data ---------------------------------------------------------------
  function applySummary(text) {
    var parsed = Model.parseJson(text)
    if (!parsed || !Array.isArray(parsed.clusters)) {
      summaryError = "summary.json is not valid triage data."
      return
    }
    summaryError = ""
    summary = parsed
    searchIndex = Model.buildIndex(parsed)
    refilter()
  }

  function applyReviews(text) {
    if (text === lastReviewsText) return
    lastReviewsText = text
    var parsed = Model.parseJson(text)
    reviews = parsed && parsed.reviews ? parsed.reviews : ({})
  }

  FileView {
    id: summaryFile
    path: root.triageDir + "/out/summary.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.applySummary(text())
    onLoadFailed: {
      if (!root.summary)
        root.summaryError = "No triage data at " + root.triageDir + "/out/summary.json. Press Rebuild."
    }
  }

  FileView {
    id: indexFile
    path: root.triageDir + "/reviews/index.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.applyReviews(text())
    onLoadFailed: root.applyReviews("")
  }

  // index.json may not exist when the watch is set up, and the age labels
  // need to tick; poll faster while the popup is showing.
  Timer {
    interval: root.opened ? 15000 : 60000
    running: true
    repeat: true
    onTriggered: {
      root.now = Date.now()
      indexFile.reload()
    }
  }

  // ---- commands -----------------------------------------------------------
  function rebuild() {
    if (buildProc.running) return
    buildError = ""
    buildProc.running = true
  }

  Process {
    id: buildProc
    command: [root.script, "build"]
    workingDirectory: root.triageDir
    stderr: StdioCollector { id: buildErr }
    onExited: function(exitCode) {
      if (exitCode === 0) {
        summaryFile.reload()
        indexFile.reload()
      } else {
        buildFailTimer.code = exitCode
        buildFailTimer.restart()
      }
    }
  }

  // Lets the stderr collector finish before its text is read.
  Timer {
    id: buildFailTimer
    property int code: 0
    interval: 150
    onTriggered: root.buildError = "Rebuild failed (exit " + code + ")\n" + Model.tailLines(buildErr.text, 4)
  }

  function run(command) {
    if (root.bar) root.bar.run(command)
  }

  function openUrl(url) {
    if (url) run("xdg-open " + Util.shellQuote(url))
  }

  function openHtml(clusterId) {
    var url = "file://" + encodeURI(triageDir + "/out/index.html") + (clusterId ? "#" + clusterId : "")
    openUrl(url)
  }

  function askAgent(cluster) {
    if (!cluster || !cluster.prs || cluster.prs.length === 0) return
    run(Util.shellQuote(script) + " agent " + Util.shellQuote(String(cluster.prs[0])))
    agentReloadTimer.count = 0
    agentReloadTimer.restart()
  }

  // The agent command returns within seconds; index.json flips to "running"
  // shortly after.
  Timer {
    id: agentReloadTimer
    property int count: 0
    interval: 3000
    repeat: true
    onTriggered: {
      indexFile.reload()
      if (++count >= 5) stop()
    }
  }

  // ---- popup --------------------------------------------------------------
  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: root.detailOpen ? keyCatcher : searchField
    contentWidth: panel.fittedContentWidth(Style.space(600))
    contentHeight: panel.fittedContentHeight(Style.space(640))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      blocked: searchField.activeFocus
      onCloseRequested: root.detailOpen ? root.back() : root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onMoveRequested: function(dx, dy) {
        if (!root.detailOpen) return
        if (dx < 0) root.back()
        else if (dy !== 0)
          detailFlick.contentY = Math.max(0, Math.min(detailFlick.contentHeight - detailFlick.height, detailFlick.contentY + dy * Style.space(60)))
      }
      onTextKey: function(t) {
        if (!root.detailOpen) return
        if (t === "b") root.openHtml(root.selected.id)
        else if (t === "o") {
          var r = root.reviews[root.selected.review_key]
          if (r && r.review_md) root.openUrl(r.review_md)
        }
      }

      // ---- header --------------------------------------------------------
      Item {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: Math.max(titleRow.implicitHeight, rebuildButton.implicitHeight)

        Row {
          id: titleRow
          anchors.left: parent.left
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(10)

          Button {
            visible: root.detailOpen
            anchors.verticalCenter: parent.verticalCenter
            iconText: "\uf053"
            foreground: root.fg
            fontFamily: root.fontFamily
            iconSize: Style.font.body
            horizontalPadding: Style.space(8)
            onClicked: root.back()
          }

          Text {
            anchors.verticalCenter: parent.verticalCenter
            textFormat: Text.PlainText
            text: "PR Triage"
            color: root.fg
            font.family: root.fontFamily
            font.pixelSize: Style.font.heading
            font.bold: true
          }

          Text {
            anchors.verticalCenter: parent.verticalCenter
            textFormat: Text.PlainText
            visible: text !== ""
            text: root.summary ? "snapshot " + root.snapshotAge : ""
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.bodySmall
          }
        }

        Button {
          id: rebuildButton
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          enabled: !root.building
          opacity: enabled ? 1 : 0.6
          iconText: "\uf021"
          iconSpinning: root.building
          text: root.building ? "Rebuilding\u2026" : "Rebuild"
          foreground: root.fg
          fontFamily: root.fontFamily
          fontSize: Style.font.bodySmall
          iconSize: Style.font.bodySmall
          bordered: true
          onClicked: root.rebuild()
        }
      }

      Text {
        id: errorText
        anchors.top: header.bottom
        anchors.topMargin: visible ? Style.space(8) : 0
        anchors.left: parent.left
        anchors.right: parent.right
        visible: text !== ""
        height: visible ? implicitHeight : 0
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        maximumLineCount: 5
        elide: Text.ElideRight
        text: root.buildError || root.summaryError
        color: root.urgentTone
        font.family: root.fontFamily
        font.pixelSize: Style.font.bodySmall
      }

      // ---- footer caveat ---------------------------------------------------
      Text {
        id: caveat
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        textFormat: Text.PlainText
        horizontalAlignment: Text.AlignHCenter
        text: "Clusters are leads, not verdicts \u2014 test before recommending."
        color: root.dim
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
        font.italic: true
      }

      Item {
        id: body
        anchors.top: errorText.bottom
        anchors.topMargin: Style.space(10)
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: caveat.top
        anchors.bottomMargin: Style.space(8)

        // ================= list view =================
        Item {
          id: listPage
          anchors.fill: parent
          visible: !root.detailOpen

          Rectangle {
            id: overviewCard
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            visible: root.summary !== null
            height: visible ? overviewColumn.implicitHeight + Style.space(20) : 0
            radius: Style.cornerRadius
            color: root.cardFill

            Column {
              id: overviewColumn
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.leftMargin: Style.space(12)
              anchors.rightMargin: Style.space(12)
              spacing: Style.space(4)

              Repeater {
                model: root.summary && root.summary.overview ? root.summary.overview.lines : []

                Text {
                  required property string modelData
                  width: overviewColumn.width
                  textFormat: Text.PlainText
                  wrapMode: Text.Wrap
                  text: modelData
                  color: root.fg
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                }
              }
            }
          }

          TextField {
            id: searchField
            anchors.top: overviewCard.bottom
            anchors.topMargin: Style.space(10)
            anchors.left: parent.left
            anchors.right: parent.right
            placeholderText: "Search PR number, author, words, file path"
            foreground: root.fg
            font.family: root.fontFamily
            font.pixelSize: Style.font.body

            onTextChanged: root.query = text

            Keys.onPressed: function(event) {
              if (event.key === Qt.Key_Escape) {
                if (text !== "") text = ""
                else root.close()
                event.accepted = true
              } else if (event.key === Qt.Key_Down) {
                root.moveList(1)
                event.accepted = true
              } else if (event.key === Qt.Key_Up) {
                root.moveList(-1)
                event.accepted = true
              } else if (event.key === Qt.Key_PageDown) {
                root.moveList(6)
                event.accepted = true
              } else if (event.key === Qt.Key_PageUp) {
                root.moveList(-6)
                event.accepted = true
              } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                root.openCluster(root.filtered[root.listIndex])
                event.accepted = true
              } else if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) {
                root.switchPanel((event.modifiers & Qt.ShiftModifier) || event.key === Qt.Key_Backtab ? -1 : 1)
                event.accepted = true
              }
            }
          }

          Text {
            id: countText
            anchors.top: searchField.bottom
            anchors.topMargin: Style.space(6)
            anchors.left: parent.left
            anchors.leftMargin: Style.space(2)
            textFormat: Text.PlainText
            text: !root.summary ? ""
              : (root.query.trim() === ""
                ? Model.plural(root.filtered.length, "cluster") + " in triage order"
                : root.filtered.length + " of " + root.summary.clusters.length + " clusters match")
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          ListView {
            id: clusterList
            anchors.top: countText.bottom
            anchors.topMargin: Style.space(4)
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            clip: true
            spacing: Style.space(2)
            boundsBehavior: Flickable.StopAtBounds
            model: root.filtered

            delegate: CursorSurface {
              id: row
              required property var modelData
              required property int index
              readonly property var status: root.statusFor(modelData)

              width: ListView.view.width
              height: rowColumn.implicitHeight + Style.space(14)
              foreground: root.fg
              hasCursor: index === root.listIndex

              Column {
                id: rowColumn
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: Style.space(12)
                anchors.rightMargin: Style.space(12)
                spacing: Style.space(4)

                Item {
                  width: parent.width
                  height: Math.max(idRow.implicitHeight, statusChip.implicitHeight)

                  Row {
                    id: idRow
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Style.space(8)

                    Text {
                      anchors.verticalCenter: parent.verticalCenter
                      textFormat: Text.PlainText
                      text: row.modelData.id
                      color: root.fg
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.body
                      font.bold: true
                    }
                    Text {
                      anchors.verticalCenter: parent.verticalCenter
                      textFormat: Text.PlainText
                      text: Model.plural(row.modelData.size, "PR")
                      color: root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.bodySmall
                    }
                    Chip {
                      anchors.verticalCenter: parent.verticalCenter
                      label: Model.kindLabel(row.modelData.kind)
                      tone: root.toneForKind(row.modelData.kind)
                      fontFamily: root.fontFamily
                    }
                  }

                  Chip {
                    id: statusChip
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    label: row.status.label
                    tone: root.toneForState(row.status.state)
                    fontFamily: root.fontFamily
                    bold: row.status.state !== "none"
                  }
                }

                Text {
                  width: parent.width
                  textFormat: Text.PlainText
                  elide: Text.ElideRight
                  text: (row.modelData.topic_words || []).join(", ")
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.bodySmall
                }
              }

              MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onPositionChanged: root.listIndex = row.index
                onClicked: {
                  root.listIndex = row.index
                  root.openCluster(row.modelData)
                }
              }
            }

            Text {
              anchors.centerIn: parent
              visible: root.summary !== null && root.filtered.length === 0
              textFormat: Text.PlainText
              text: "No clusters match \u201c" + root.query.trim() + "\u201d"
              color: root.dim
              font.family: root.fontFamily
              font.pixelSize: Style.font.body
            }
          }
        }

        // ================= detail view =================
        Item {
          id: detailPage
          anchors.fill: parent
          visible: root.detailOpen

          readonly property var cluster: root.selected
          readonly property var review: cluster ? (root.reviews[cluster.review_key] || null) : null
          readonly property var status: root.statusFor(cluster)
          readonly property bool hasReview: status.state !== "none"

          Row {
            id: actionBar
            anchors.bottom: parent.bottom
            anchors.left: parent.left
            spacing: Style.space(8)

            Button {
              readonly property bool agentRunning: detailPage.status.state === "running"
              enabled: !agentRunning
              opacity: enabled ? 1 : 0.6
              iconText: "\uf120"
              text: agentRunning ? "Agent running\u2026"
                : (detailPage.hasReview ? "Ask agent to re-review" : "Ask agent to review")
              foreground: root.fg
              fontFamily: root.fontFamily
              fontSize: Style.font.bodySmall
              iconSize: Style.font.bodySmall
              bordered: true
              onClicked: root.askAgent(detailPage.cluster)
            }

            Button {
              iconText: "\uf08e"
              text: "Open in browser"
              foreground: root.fg
              fontFamily: root.fontFamily
              fontSize: Style.font.bodySmall
              iconSize: Style.font.bodySmall
              bordered: true
              onClicked: root.openHtml(detailPage.cluster ? detailPage.cluster.id : "")
            }
          }

          Flickable {
            id: detailFlick
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: actionBar.top
            anchors.bottomMargin: Style.space(10)
            contentWidth: width
            contentHeight: detailColumn.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            interactive: contentHeight > height

            Column {
              id: detailColumn
              width: detailFlick.width
              spacing: Style.space(12)

              // -- headline
              Column {
                width: parent.width
                spacing: Style.space(6)

                Row {
                  spacing: Style.space(8)
                  Text {
                    anchors.verticalCenter: parent.verticalCenter
                    textFormat: Text.PlainText
                    text: detailPage.cluster ? detailPage.cluster.id : ""
                    color: root.fg
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.title
                    font.bold: true
                  }
                  Chip {
                    anchors.verticalCenter: parent.verticalCenter
                    label: detailPage.cluster ? Model.kindLabel(detailPage.cluster.kind) : ""
                    tone: detailPage.cluster ? root.toneForKind(detailPage.cluster.kind) : root.dim
                    fontFamily: root.fontFamily
                  }
                  Chip {
                    anchors.verticalCenter: parent.verticalCenter
                    label: detailPage.status.label
                    tone: root.toneForState(detailPage.status.state)
                    fontFamily: root.fontFamily
                    bold: detailPage.hasReview
                  }
                }

                Text {
                  width: parent.width
                  textFormat: Text.PlainText
                  wrapMode: Text.Wrap
                  text: detailPage.cluster ? detailPage.cluster.headline : ""
                  color: root.fg
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                }

                Text {
                  width: parent.width
                  textFormat: Text.PlainText
                  wrapMode: Text.WrapAnywhere
                  visible: detailPage.cluster !== null
                  text: detailPage.cluster
                    ? Model.plural(detailPage.cluster.authors, "author") + " \u00b7 opened "
                      + detailPage.cluster.opened_first + " \u2013 " + detailPage.cluster.opened_last
                      + (detailPage.cluster.conflicting.length ? " \u00b7 " + detailPage.cluster.conflicting.length + " conflicting" : "")
                      + (detailPage.cluster.stale_30d.length ? " \u00b7 " + detailPage.cluster.stale_30d.length + " stale 30d+" : "")
                      + "\n" + (detailPage.cluster.hot_paths || []).join("\n")
                    : ""
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }

              // -- agent review
              Rectangle {
                width: parent.width
                visible: detailPage.hasReview
                height: visible ? reviewColumn.implicitHeight + Style.space(20) : 0
                radius: Style.cornerRadius
                color: root.cardFill
                border.width: 1
                border.color: Util.alpha(root.toneForState(detailPage.status.state), 0.5)

                Column {
                  id: reviewColumn
                  anchors.left: parent.left
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  anchors.leftMargin: Style.space(12)
                  anchors.rightMargin: Style.space(12)
                  spacing: Style.space(6)

                  Text {
                    textFormat: Text.PlainText
                    text: "Agent review"
                    color: root.fg
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.body
                    font.bold: true
                  }

                  Text {
                    width: parent.width
                    visible: detailPage.status.state === "running" || detailPage.status.state === "abandoned"
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    text: {
                      var r = detailPage.review
                      var when = r && r.started_at ? Model.ageText(r.started_at, root.now) : "earlier"
                      return detailPage.status.state === "running"
                        ? "An agent started reviewing this cluster " + when + "."
                        : "An agent started " + when + " but never finished. Ask again to retry."
                    }
                    color: root.dim
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                  }

                  Text {
                    width: parent.width
                    visible: detailPage.status.state === "stale"
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    text: "The cluster's PR set changed since this review (it covered "
                      + (detailPage.review ? (detailPage.review.prs || []).map(function(n) { return "#" + n }).join(", ") : "")
                      + "). Treat it as outdated."
                    color: root.warnTone
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                  }

                  Text {
                    width: parent.width
                    visible: detailPage.review && detailPage.review.status === "done" && text !== ""
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    text: detailPage.review && detailPage.review.summary ? detailPage.review.summary : ""
                    color: root.fg
                    font.family: root.fontFamily
                    font.pixelSize: Style.font.bodySmall
                  }

                  Row {
                    visible: detailPage.review && detailPage.review.status === "done"
                    spacing: Style.space(6)

                    Chip {
                      visible: detailPage.review && detailPage.review.core_pr
                      label: detailPage.review ? "core #" + detailPage.review.core_pr : ""
                      tone: root.goodTone
                      fontFamily: root.fontFamily
                    }
                    Chip {
                      label: detailPage.review ? "confidence " + detailPage.review.confidence : ""
                      tone: detailPage.review && detailPage.review.confidence === "high" ? root.goodTone
                        : (detailPage.review && detailPage.review.confidence === "low" ? root.warnTone : root.dim)
                      fontFamily: root.fontFamily
                    }
                    Chip {
                      label: detailPage.review ? Model.plural(detailPage.review.actions || 0, "action") : ""
                      tone: root.dim
                      fontFamily: root.fontFamily
                    }
                    Text {
                      anchors.verticalCenter: parent.verticalCenter
                      textFormat: Text.PlainText
                      text: detailPage.review && detailPage.review.reviewed_at
                        ? "reviewed " + Model.ageText(detailPage.review.reviewed_at, root.now) : ""
                      color: root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                    }
                  }

                  Button {
                    visible: detailPage.review && detailPage.review.status === "done" && !!detailPage.review.review_md
                    iconText: "\uf15c"
                    text: "Open review"
                    foreground: root.fg
                    fontFamily: root.fontFamily
                    fontSize: Style.font.bodySmall
                    iconSize: Style.font.bodySmall
                    bordered: true
                    onClicked: root.openUrl(detailPage.review.review_md)
                  }
                }
              }

              // -- why
              Column {
                width: parent.width
                spacing: Style.space(6)
                visible: detailPage.cluster && detailPage.cluster.why && detailPage.cluster.why.length > 0

                Text {
                  textFormat: Text.PlainText
                  text: "WHY THESE ARE GROUPED"
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  font.letterSpacing: 1
                }

                Repeater {
                  model: detailPage.cluster ? detailPage.cluster.why : []

                  Row {
                    required property string modelData
                    width: detailColumn.width
                    spacing: Style.space(8)

                    Text {
                      textFormat: Text.PlainText
                      text: "\u2022"
                      color: root.dim
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.bodySmall
                    }
                    Text {
                      width: parent.width - Style.space(16)
                      textFormat: Text.PlainText
                      wrapMode: Text.Wrap
                      maximumLineCount: 4
                      elide: Text.ElideRight
                      text: modelData
                      color: root.fg
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.bodySmall
                    }
                  }
                }
              }

              // -- PRs
              Column {
                width: parent.width
                spacing: Style.space(2)

                Text {
                  textFormat: Text.PlainText
                  bottomPadding: Style.space(4)
                  text: detailPage.cluster ? Model.plural(detailPage.cluster.prs.length, "PR").toUpperCase() : ""
                  color: root.dim
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.caption
                  font.letterSpacing: 1
                }

                Repeater {
                  model: detailPage.cluster ? detailPage.cluster.prs : []

                  CursorSurface {
                    id: prRow
                    required property int modelData
                    readonly property var pr: root.summary && root.summary.prs ? root.summary.prs[String(modelData)] : null

                    width: detailColumn.width
                    height: prColumn.implicitHeight + Style.space(12)
                    foreground: root.fg
                    hasCursor: prArea.containsMouse

                    Column {
                      id: prColumn
                      anchors.left: parent.left
                      anchors.right: parent.right
                      anchors.verticalCenter: parent.verticalCenter
                      anchors.leftMargin: Style.space(10)
                      anchors.rightMargin: Style.space(10)
                      spacing: Style.space(4)

                      Item {
                        width: parent.width
                        height: Math.max(prNum.implicitHeight, prTitle.implicitHeight)

                        Text {
                          id: prNum
                          anchors.left: parent.left
                          textFormat: Text.PlainText
                          text: "#" + prRow.modelData
                          color: root.fg
                          font.family: root.fontFamily
                          font.pixelSize: Style.font.bodySmall
                          font.bold: true
                        }
                        Text {
                          id: prTitle
                          anchors.left: prNum.right
                          anchors.leftMargin: Style.space(8)
                          anchors.right: parent.right
                          textFormat: Text.PlainText
                          elide: Text.ElideRight
                          text: prRow.pr ? prRow.pr.title : "(not in snapshot)"
                          color: root.fg
                          font.family: root.fontFamily
                          font.pixelSize: Style.font.bodySmall
                        }
                      }

                      Row {
                        spacing: Style.space(8)

                        Text {
                          anchors.verticalCenter: parent.verticalCenter
                          textFormat: Text.PlainText
                          text: prRow.pr ? prRow.pr.author : ""
                          color: root.dim
                          font.family: root.fontFamily
                          font.pixelSize: Style.font.caption
                        }
                        Text {
                          anchors.verticalCenter: parent.verticalCenter
                          textFormat: Text.PlainText
                          visible: prRow.pr !== null
                          text: !prRow.pr ? ""
                            : (prRow.pr.merges_cleanly === true ? "\uf00c merges cleanly"
                              : (prRow.pr.merges_cleanly === false ? "\uf00d conflicts" : "\uf128 mergeability unknown"))
                          color: !prRow.pr ? root.dim
                            : (prRow.pr.merges_cleanly === true ? root.goodTone
                              : (prRow.pr.merges_cleanly === false ? root.urgentTone : root.dim))
                          font.family: root.fontFamily
                          font.pixelSize: Style.font.caption
                        }
                        Chip {
                          anchors.verticalCenter: parent.verticalCenter
                          visible: Model.isVerified(prRow.pr)
                          label: "verified"
                          tone: root.goodTone
                          fontFamily: root.fontFamily
                        }
                        Chip {
                          anchors.verticalCenter: parent.verticalCenter
                          visible: prRow.pr !== null && prRow.pr.draft === true
                          label: "draft"
                          tone: root.dim
                          fontFamily: root.fontFamily
                        }
                        Chip {
                          anchors.verticalCenter: parent.verticalCenter
                          visible: detailPage.review !== null && detailPage.review.core_pr === prRow.modelData
                          label: "\uf005 core"
                          tone: root.goodTone
                          fontFamily: root.fontFamily
                        }
                      }
                    }

                    MouseArea {
                      id: prArea
                      anchors.fill: parent
                      hoverEnabled: true
                      cursorShape: Qt.PointingHandCursor
                      onClicked: if (prRow.pr) root.openUrl(prRow.pr.url)
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
