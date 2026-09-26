import QtQuick
import QtTest
import Quickshell
import "views" as Views

ShellRoot {
  QtObject {
    id: service
    property string trackId: ""
    property var callbacks: []
    function request(op, args, callback) { callbacks.push(callback) }
  }
  FloatingWindow {
    visible: true
    implicitWidth: 600
    implicitHeight: 500
    Views.DetailView { id: detail; anchors.fill: parent; svc: service }
    TestCase {
      id: tester
      name: "DetailRequests"
      when: false
      function test_reopen() {
        detail.open({ id: "album:1" })
        var stale = service.callbacks.pop()
        detail.page = null
        detail.open({ id: "album:1" })
        var fresh = service.callbacks.pop()
        fresh({ ok: true, data: { title: "New account", tracks: [] } })
        stale({ ok: true, data: { title: "Old account", tracks: [] } })
        compare(detail.info.title, "New account")
        detail.open({ id: "album:2" })
        detail.open({ id: "album:3", info: { title: "Cached", tracks: [] } })
        verify(!detail.busy, "cached pages must clear the loading state")
        detail.info = { tracks: [], continuation: "50" }
        detail.loadMore()
        var more = service.callbacks.pop()
        detail.open({ id: "album:4", info: { tracks: [] } })
        verify(!detail.loadingMore)
        detail.info = { tracks: [], continuation: "50" }
        detail.loadMore()
        more({ ok: true, data: { tracks: [], continuation: "" } })
        verify(detail.loadingMore, "an old callback must not clear a new request's loading flag")
        console.log("DETAIL_OK")
        Qt.quit()
      }
    }
    Timer { interval: 300; running: true; onTriggered: { try { tester.test_reopen() } catch (e) { console.log("TEST_ERROR " + e); Qt.quit() } } }
    Timer { interval: 8000; running: true; onTriggered: Qt.quit() }
  }
}
