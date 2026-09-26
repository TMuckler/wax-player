import QtQuick
import QtTest
import Quickshell
import qs.Commons
import "views" as Views
import "lib/Model.js" as Model

ShellRoot {
  QtObject {
    id: fakeSvc
    property bool bridgeUp: true
    property bool signedIn: false
    property var account: ({ url: "", username: "" })
    property var accountDetails: ({ name: "", email: "", avatar: "" })
    property var settings: Model.SETTINGS_DEFAULTS
    property var submitted: []
    property string sleepMode: "off"
    property string waxVersion: "2.0.0"
    function connectServer(url, user, secret, cb) {
      submitted.push({ url: url, user: user, secret: secret })
      account = { url: url, username: user }
      signedIn = true
      cb({ ok: true })
    }
    function accountInfo(cb) { if (cb) cb({ ok: true, data: accountDetails }) }
    function accountSignOut(cb) { signedIn = false; account = { url: "", username: "" }; cb({ ok: true }) }
    function setting(key, fallback) { return settings[key] === undefined ? fallback : settings[key] }
    function saveSetting(key, value) { var copy = Object.assign({}, settings); copy[key] = value; settings = copy }
    function engineVersion(cb) { cb({ ok: true, data: { product: "mpv / Navidrome" } }) }
    function clearCache(cb) { cb({ ok: true }) }
    function resetSettings() { settings = Model.SETTINGS_DEFAULTS }
    function nudgeSleepMode(dir) { sleepMode = Model.nextSleepOption(sleepMode, dir) }
  }
  FloatingWindow {
    id: win
    visible: true
    implicitWidth: 560
    implicitHeight: 640
    color: Color.popups.background
    Rectangle {
      id: stage
      anchors.fill: parent
      color: Color.popups.background
      Views.SettingsView {
        id: settingsView
        anchors.fill: parent
        anchors.margins: 20
        svc: fakeSvc
      }
    }
    function find(item, name) {
      if (item.objectName === name) return item
      for (var i = 0; i < item.children.length; i++) { var found = find(item.children[i], name); if (found) return found }
      return null
    }
    TestCase {
      id: tester
      name: "NavidromeConnection"
      when: false
      function test_connection() {
        var server = win.find(settingsView, "serverUrl")
        var username = win.find(settingsView, "serverUsername")
        var password = win.find(settingsView, "serverPassword")
        var connectButton = win.find(settingsView, "serverConnect")
        verify(server !== null && username !== null && password !== null && connectButton !== null)
        server.text = "https://music.example.test/base"
        username.text = "listener"
        password.text = "test-only-secret"
        compare(password.echoMode, TextInput.Password)
        mouseClick(connectButton, connectButton.width / 2, connectButton.height / 2)
        compare(fakeSvc.submitted.length, 1)
        compare(fakeSvc.submitted[0].url, server.text)
        compare(fakeSvc.submitted[0].secret, "test-only-secret")
        compare(password.text, "")
        verify(fakeSvc.signedIn)
        settingsView.navIndex = 1
        settingsView.cursor = 0
        settingsView.act()
        verify(fakeSvc.settings.eqEnabled)
        settingsView.navIndex = 0
        var out = Quickshell.env("WAX_CONNECTION_OUT")
        if (out) {
          stage.grabToImage(function (image) { image.saveToFile(out); console.log("CONNECTION_OK"); Qt.quit() })
        } else { console.log("CONNECTION_OK"); Qt.quit() }
      }
    }
    Timer { interval: 300; running: true; onTriggered: { try { tester.test_connection() } catch (e) { console.log("TEST_ERROR " + e + " " + e.stack); Qt.quit() } } }
    Timer { interval: 8000; running: true; onTriggered: { console.log("CONNECTION_TIMEOUT"); Qt.quit() } }
  }
}
