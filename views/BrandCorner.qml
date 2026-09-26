import QtQuick
import qs.Ui
import qs.Commons
import "../lib/Icons.js" as Icons

// Wax's name, small and quiet, the gear (Settings) and the power button:
// power exits Wax (bridge and mpv) and, once closed, starts
// it again. Quiet like the name until the pointer comes; lit while closed
// or, for the gear, while Settings is open.
Row {
  id: root

  property var svc: null
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family
  property bool settingsOpen: false

  signal gearClicked()

  readonly property bool closed: svc ? svc.closed === true : false
  readonly property bool reachable: svc ? svc.bridgeUp === true && !svc.signingIn : false

  // Connection setup is available before playback starts.
  readonly property bool showSignIn: svc ? svc.bridgeUp && !svc.signedIn : false
  readonly property bool showPremium: false

  spacing: Style.space(4)

  HitButton {
    id: signIn
    objectName: "signInButton"
    anchors.verticalCenter: parent.verticalCenter
    minSize: Style.space(28)
    text: "Connect"
    fontFamily: root.fontFamily
    fontSize: Style.font.caption
    foreground: root.foreground
    bordered: true
    visible: root.showSignIn
    onClicked: if (root.svc) root.svc.signIn()
  }
  Text {
    anchors.verticalCenter: parent.verticalCenter
    opacity: 0.4
    text: Icons.album
    textFormat: Text.PlainText
    color: root.foreground
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
  }
  Text {
    anchors.verticalCenter: parent.verticalCenter
    opacity: 0.4
    text: "Wax"
    textFormat: Text.PlainText
    color: root.foreground
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
  }
  HitButton {
    id: gear
    objectName: "gearButton"
    anchors.verticalCenter: parent.verticalCenter
    minSize: Style.space(28)
    iconText: Icons.gear
    iconSize: Style.font.body
    foreground: root.settingsOpen ? Color.accent : root.foreground
    selected: root.settingsOpen
    visible: root.reachable
    opacity: root.settingsOpen || hot ? 1 : 0.45
    Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
    tooltipText: "Settings"
    onClicked: root.gearClicked()
  }
  HitButton {
    id: power
    objectName: "powerButton"
    anchors.verticalCenter: parent.verticalCenter
    minSize: Style.space(28)
    iconText: Icons.power
    iconSize: Style.font.body
    foreground: root.closed ? Color.accent : root.foreground
    selected: root.closed
    visible: root.reachable || root.closed
    opacity: root.closed || hot ? 1 : 0.45
    Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
    tooltipText: root.closed ? "Turn Wax on" : "Quit Wax — stop all player processes"
    onClicked: if (root.svc) root.svc.toggleEngine()
  }
}
