import QtQuick
import qs.Ui
import qs.Commons
import "../lib/Model.js" as Model

// Connection credentials travel only over the private bridge socket.
Column {
  id: card
  property var svc: null
  property QtObject bar: null
  readonly property color fg: bar ? bar.foreground : Color.foreground
  readonly property string family: bar ? bar.fontFamily : Style.font.family
  readonly property bool inputFocused: server.activeFocus || username.activeFocus || password.activeFocus
  property bool busy: false
  property string error: ""
  spacing: Style.space(10)

  function refresh() {
    if (!svc) return
    server.text = svc.account.url || ""
    username.text = svc.account.username || ""
    password.text = ""
    error = ""
  }
  Component.onCompleted: refresh()
  onVisibleChanged: { password.text = ""; if (visible) refresh() }
  Connections {
    target: card.svc
    function onAccountChanged() { if (!card.busy) card.refresh() }
  }
  function connect() {
    if (!svc || busy) return
    busy = true
    error = ""
    svc.connectServer(server.text.trim(), username.text.trim(), password.text, function (r) {
      card.busy = false
      password.text = ""
      card.error = r.ok ? "Connected" : Model.errorText(r.error)
    })
  }
  Text {
    width: parent.width
    text: "Navidrome connection"
    textFormat: Text.PlainText
    color: card.fg
    font.family: card.family
    font.pixelSize: Style.font.title
    font.bold: true
  }
  TextField {
    id: server
    objectName: "serverUrl"
    width: parent.width
    placeholderText: "Server URL, e.g. https://music.example.com"
    font.family: card.family
    foreground: card.fg
    background: Rectangle {
      color: Util.alpha(card.fg, 0.08)
      border.color: Util.alpha(card.fg, 0.35)
      radius: Style.spacing.labelGap
    }
    enabled: !card.busy
    onAccepted: username.forceActiveFocus()
  }
  TextField {
    id: username
    objectName: "serverUsername"
    width: parent.width
    placeholderText: "Username"
    font.family: card.family
    foreground: card.fg
    background: Rectangle {
      color: Util.alpha(card.fg, 0.08)
      border.color: Util.alpha(card.fg, 0.35)
      radius: Style.spacing.labelGap
    }
    enabled: !card.busy
    onAccepted: password.forceActiveFocus()
  }
  TextField {
    id: password
    objectName: "serverPassword"
    width: parent.width
    placeholderText: card.svc && card.svc.account.url ? "Password (blank keeps saved credentials)" : "Password"
    password: true
    font.family: card.family
    foreground: card.fg
    background: Rectangle {
      color: Util.alpha(card.fg, 0.08)
      border.color: Util.alpha(card.fg, 0.35)
      radius: Style.spacing.labelGap
    }
    enabled: !card.busy
    onAccepted: card.connect()
  }
  Text {
    width: parent.width
    text: "Use your Navidrome account. Include any server base path in the URL. HTTPS is required except for numeric loopback addresses on this machine."
    textFormat: Text.PlainText
    wrapMode: Text.WordWrap
    color: Util.alpha(card.fg, 0.65)
    font.family: card.family
    font.pixelSize: Style.font.caption
  }
  Row {
    spacing: Style.space(8)
    Button {
      objectName: "serverConnect"
      text: card.busy ? "Connecting…" : "Connect"
      enabled: !card.busy && !!card.svc && card.svc.bridgeUp && server.text.trim() !== "" && username.text.trim() !== ""
      foreground: card.fg
      fontFamily: card.family
      bordered: true
      onClicked: card.connect()
    }
    Button {
      text: "Disconnect"
      visible: !!card.svc && !!card.svc.account.url
      enabled: !card.busy
      foreground: card.fg
      fontFamily: card.family
      onClicked: card.svc.accountSignOut(function (r) { card.error = r.ok ? "Disconnected" : Model.errorText(r.error) })
    }
  }
  Text {
    width: parent.width
    text: card.error
    visible: text !== ""
    textFormat: Text.PlainText
    wrapMode: Text.WordWrap
    color: Color.accent
    font.family: card.family
    font.pixelSize: Style.font.caption
  }
}
