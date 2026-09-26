import QtQuick
import QtTest
import Quickshell
import "views" as Views

ShellRoot {
  QtObject {
    id: service
    property string trackId: ""
    property bool bridgeUp: true
    property bool ready: true
    property bool signedIn: true
    property var account: ({ url: "http://test", username: "test" })
    property var callbacks: []
    signal libraryChanged()
    function request(op, args, callback) { callbacks.push(callback) }
  }
  FloatingWindow {
    visible: true
    implicitWidth: 600
    implicitHeight: 500
    Views.SearchView { id: search; anchors.fill: parent; svc: service }
    Views.LibraryView { id: library; anchors.fill: parent; svc: service; visible: false }
    TestCase {
      id: tester
      name: "ListRequests"
      when: false
      function test_search() {
        search.query = "old"
        search.run()
        var stale = service.callbacks.pop()
        search.query = "new"
        // The next search is still waiting for the typing debounce timer.
        stale({ ok: true, data: { songs: [], continuation: "50" } })
        compare(search.result, null, "old results appeared under a new query")
        search.run()
        var fresh = service.callbacks.pop()
        fresh({ ok: true, data: { songs: [], continuation: "50" } })
        verify(search.result !== null)
        search.query = "third"
        compare(search.result, null, "old pagination survived a query change")
      }
      function test_library() {
        library.section = "songs"
        library.load()
        var stale = service.callbacks.pop()
        service.libraryChanged()
        library.load()
        var fresh = service.callbacks.pop()
        fresh({ ok: true, data: { title: "New favorites", tracks: [] } })
        stale({ ok: true, data: { title: "Old favorites", tracks: [] } })
        compare(library.info.title, "New favorites")
        library.load()
        var songs = service.callbacks.pop()
        library.setSection("albums")
        songs({ ok: false, error: "server-unreachable" })
        verify(library.busy, "old section request cleared the new loading indicator")
        compare(library.error, "")
        service.signedIn = false
        service.callbacks.pop()({ ok: true, data: { title: "Private albums", tracks: [] } })
        compare(library.info, null, "a response repopulated the cache after sign-out")
      }
    }
    Timer {
      interval: 300; running: true
      onTriggered: {
        var passed = true
        try { tester.test_search() } catch (e) { passed = false; console.log("SEARCH_FAILED " + e) }
        try { tester.test_library() } catch (e) { passed = false; console.log("LIBRARY_FAILED " + e) }
        if (passed) console.log("LISTS_OK")
        Qt.quit()
      }
    }
    Timer { interval: 8000; running: true; onTriggered: Qt.quit() }
  }
}
