import QtQuick
import qs.Commons

// Small rounded status/label badge. `tone` tints text, border and fill.
Rectangle {
  id: root

  property string label: ""
  property color tone: Color.foreground
  property string fontFamily: Style.font.family
  property real fontSize: Style.font.caption
  property bool bold: false

  implicitWidth: text.implicitWidth + Style.space(10)
  implicitHeight: text.implicitHeight + Style.space(4)
  radius: Math.min(Style.cornerRadius, height / 2)
  color: Util.alpha(tone, 0.14)
  border.width: 1
  border.color: Util.alpha(tone, 0.45)

  Text {
    id: text
    anchors.centerIn: parent
    textFormat: Text.PlainText
    text: root.label
    color: root.tone
    font.family: root.fontFamily
    font.pixelSize: root.fontSize
    font.bold: root.bold
  }
}
