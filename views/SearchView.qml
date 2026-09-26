import QtQuick
import qs.Ui
import qs.Commons
import "../lib/Model.js" as Model

// Search: a field, filter chips, and one list of results. With an empty
// field it shows Navidrome's home shelves, so there is always
// something to play.
Item {
  id: view

  property var svc: null
  property QtObject bar: null
  property var panel: null
  readonly property color fg: bar ? bar.foreground : Color.foreground
  readonly property string family: bar ? bar.fontFamily : Style.font.family

  property string query: ""
  onQueryChanged: { seq++; result = null; error = ""; busy = false }
  property string filter: ""
  property var result: null
  property var home: null
  property bool busy: false
  property string error: ""
  property int seq: 0

  readonly property bool inputFocused: field.activeFocus
  readonly property var rows: query.trim() === "" ? Model.sectionRows(home ? home.sections : [], 6) : Model.searchRows(result, filter)
  property alias cursor: list.cursor
  readonly property var current: cursor >= 0 && cursor < rows.length ? rows[cursor] : null
  readonly property var hints: inputFocused
    ? [["↵", "search"], ["↓", "results"], ["esc", "clear"]]
    : [["↵", "play or open"], ["e", "play next"], ["a", "add to queue"], ["[ ]", "filter"]]

  readonly property var filters: [
    { key: "", label: "All" }, { key: "songs", label: "Songs" }, { key: "albums", label: "Albums" },
    { key: "artists", label: "Artists" }, { key: "playlists", label: "Playlists" }
  ]

  // On screen: the panel is open on this view (see QueueView).
  readonly property string accountKey: svc ? (svc.account.url || "") + "|" + (svc.account.username || "") : ""
  onAccountKeyChanged: { seq++; home = null; result = null; busy = false; if (active && ready) loadHome() }
  property bool active: false
  function shown() { if (!home && svc && svc.ready) loadHome() }
  readonly property bool ready: svc ? svc.ready : false
  onReadyChanged: if (ready && active && !home) loadHome()
  function focusInput() { field.forceActiveFocus(); field.selectAll() }
  function move(dy) { list.cursor = Model.moveCursor(rows, list.cursor, dy); if (dy > 0 && list.cursor >= rows.length - 3) loadMore() }
  function loadMore() {
    if (busy || !result || !result.continuation || !filter) return
    busy = true
    var serial = seq
    svc.request("search", { q: query.trim(), filter: filter, continuation: result.continuation }, function (r) {
      if (serial !== view.seq) return
      view.busy = false
      if (!r.ok) { view.error = Model.errorText(r.error); return }
      var next = Object.assign({}, view.result)
      next[view.filter] = (next[view.filter] || []).concat(r.data[view.filter] || [])
      next.continuation = r.data.continuation
      view.result = next
    })
  }

  function loadHome() {
    if (!svc) return
    var key = view.accountKey
    svc.request("home", {}, function (r) { if (key !== view.accountKey) return; if (r.ok) view.home = r.data; else view.error = Model.errorText(r.error) })
  }

  function run() {
    var q = view.query.trim()
    var s = ++view.seq
    view.result = null
    if (!q) { view.result = null; view.error = ""; view.busy = false; return }
    view.busy = true
    view.error = ""
    var args = { q: q }
    if (view.filter) args.filter = view.filter
    svc.request("search", args, function (r) {
      if (s !== view.seq) return
      view.busy = false
      if (r.ok) {
        view.result = r.data
        list.cursor = Model.firstRow(view.rows)
      } else {
        view.error = Model.errorText(r.error)
      }
    })
  }

  function cycleFilter(d) {
    var i = 0
    for (var k = 0; k < filters.length; k++) if (filters[k].key === view.filter) i = k
    setFilter(filters[(i + d + filters.length) % filters.length].key)
  }

  function setFilter(key) {
    view.filter = key
    if (view.query.trim()) run()
  }

  // Enter in the field: search now and move to the results.
  function acceptField() {
    debounce.stop()
    run()
    panel.focusKeys()
  }

  function clearField() {
    debounce.stop()
    if (field.text !== "") { ++view.seq; view.busy = false; field.text = ""; view.query = ""; view.result = null }
    else panel.focusKeys()
  }

  function headerActivated(index) {
    var more = rows[index] && rows[index].more
    if (more && view.query.trim()) setFilter(more)
    else if (more) panel.openPage({ browseId: more, title: rows[index].header }, rows[index].moreParams)
  }

  Timer { id: debounce; interval: 350; onTriggered: view.run() }

  Column {
    id: top
    width: parent.width
    spacing: Style.space(8)

    TextField {
      id: field
      width: parent.width
      placeholderText: "Search songs, albums, artists and playlists"
      font.family: view.family
      onTextEdited: { view.query = text; debounce.restart() }
      onAccepted: view.acceptField()
      Keys.onEscapePressed: function (e) { view.clearField(); e.accepted = true }
      Keys.onDownPressed: function (e) { view.panel.focusKeys(); if (view.cursor < 0) view.move(1); e.accepted = true }
    }

    Row {
      spacing: Style.space(4)
      visible: view.query.trim() !== ""
      Repeater {
        model: view.filters
        delegate: Button {
          required property var modelData
          text: modelData.label
          fontFamily: view.family
          fontSize: Style.font.caption
          selected: view.filter === modelData.key
          foreground: view.fg
          verticalPadding: Style.space(3)
          horizontalPadding: Style.space(8)
          onClicked: view.setFilter(modelData.key)
        }
      }
    }
  }

  RowList {
    id: list
    svc: view.svc
    anchors.top: top.bottom
    anchors.topMargin: Style.space(8)
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.bottom: parent.bottom
    bar: view.bar
    rows: view.rows
    playingId: view.svc ? view.svc.trackId : ""
    actionsFor: function (row) { return view.panel ? view.panel.itemActions(row) : [] }
    emptyText: view.busy ? "Searching" : view.error !== "" ? view.error
      : view.query.trim() !== "" ? "No results" : (view.home ? "" : "Loading suggestions")
    onAtYEndChanged: if (atYEnd) view.loadMore()
    onActivated: function (i) { view.panel.activateRow(view.rows[i]) }
    onAction: function (name, i) { view.panel.runAction(name, view.rows[i]) }
    onHeaderActivated: function (i) { view.headerActivated(i) }
  }
}
