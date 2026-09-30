import QtQuick
import Quickshell.Io
import qs.Commons
import qs.Ui

// Bar icon for PR Triage. Left click toggles the popup, middle click rebuilds
// the cluster snapshot, right click opens the full HTML view. All state lives
// in Panel.qml, which this widget hosts (same shape as the weather plugin).
BarWidget {
  id: root
  moduleName: "bay.pr-triage"

  readonly property string glyph: "\uf407"  // nf-oct-git_pull_request

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  function togglePanel() {
    if (panelLoader.item && panelLoader.item.toggle) panelLoader.item.toggle()
  }

  // Shape contract for shell.summon/hide/toggle routing.
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false

  function open() {
    if (panelLoader.item && panelLoader.item.openFromHotkey) panelLoader.item.openFromHotkey()
  }

  function close() {
    if (panelLoader.item && panelLoader.item.close) panelLoader.item.close()
  }

  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false

  function closeForPopoutSwitch() {
    if (panelLoader.item) panelLoader.item.closeForPopoutSwitch()
  }

  readonly property bool building: panelLoader.item ? panelLoader.item.building === true : false
  readonly property int readyReviews: panelLoader.item ? panelLoader.item.readyReviews : 0

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  // `qs ipc call bay.pr-triage <fn>`; handy for keybindings and scripted checks.
  IpcHandler {
    target: "bay.pr-triage"

    function open(): void { root.open() }
    function close(): void { root.close() }
    function toggle(): void { root.togglePanel() }
    function rebuild(): void { if (panelLoader.item) panelLoader.item.rebuild() }
    function search(query: string): void { if (panelLoader.item) panelLoader.item.setQuery(query) }
    function select(clusterId: string): void { if (panelLoader.item) panelLoader.item.selectById(clusterId) }
    function back(): void { if (panelLoader.item) panelLoader.item.back() }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.glyph
    slotSize: Style.bar.statusSlot
    // Tooltip suppressed because the panel is the detail view.
    tooltipText: ""

    onPressed: function(b) {
      if (!root.bar || !panelLoader.item) return
      if (b === Qt.RightButton) panelLoader.item.openHtml("")
      else if (b === Qt.MiddleButton) panelLoader.item.rebuild()
      else root.togglePanel()
    }

    // Count of clusters whose agent review is finished and current, or an
    // ellipsis while a rebuild runs.
    Rectangle {
      id: badge
      visible: root.building || root.readyReviews > 0
      anchors.right: parent.right
      anchors.top: parent.top
      anchors.rightMargin: -Style.space(2)
      anchors.topMargin: -Style.space(1)
      width: Math.max(height, badgeText.implicitWidth + Style.space(3))
      height: badgeText.implicitHeight
      radius: height / 2
      color: Color.accent

      Text {
        id: badgeText
        anchors.centerIn: parent
        textFormat: Text.PlainText
        text: root.building ? "\u2026" : String(root.readyReviews)
        color: root.bar ? root.bar.background : Color.background
        font.family: root.bar ? root.bar.fontFamily : Style.font.family
        font.pixelSize: Math.round(Style.font.caption * 0.8)
        font.bold: true
      }
    }
  }
}
