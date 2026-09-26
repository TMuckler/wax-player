import QtQuick
import QtTest
import Quickshell

ShellRoot {
  QtObject {
    id: host
    property bool panelVisible: true
    function hide(id) { if (id === "local.wax.player") panelVisible = false }
    function updateEntryInline(id, settings) {}
  }
  WaxService {
    id: service
    shell: host
    settings: ({ globalKeys: false, poweredOff: true })
    property int launches: 0
    property int stops: 0
    property var calls: []
    function startBridgeUnit() { launches++ }
    function stopBridgeUnit() { stops++ }
    function request(op, args, cb, timeoutMs) { calls.push(op); return 1 }
    function syncKeys() {}
  }
  TestCase {
    id: tester
    name: "PowerOff"
    when: false
    function checkOff() {
      service.adoptSettings({ globalKeys: false, poweredOff: true })
      verify(service.closed)
      service.startBridge()
      service.maybeRestartBridge()
      compare(service.launches, 0)
      service.startEngine()
      verify(!service.poweredOff)
      compare(service.launches, 1)
      service.stopEngine()
      compare(service.stops, 1, "quit during startup must stop the service even without IPC")
      verify(service.closed)
      verify(!host.panelVisible, "power-off left the panel open")
      verify(service.setting("poweredOff", false))
      service.bridgeUnitStarted = false
      service.maybeRestartBridge()
      compare(service.launches, 1)
      service.quitRequested = false
      verify(service.closed, "saved off state must survive clearing transient state")
    }
    function checkLater() {
      compare(service.launches, 1, "retry timers relaunched a powered-off player")
      service.startEngine()
      compare(service.launches, 2)
      host.panelVisible = true
      service.onLine(JSON.stringify({ event: "quit", data: {} }))
      verify(!host.panelVisible, "CLI/MPRIS quit left the panel open")
      verify(service.closed, "CLI/MPRIS quit must suppress restarts too")
      console.log("POWER_OK")
      Qt.quit()
    }
  }
  Timer { interval: 100; running: true; onTriggered: { try { tester.checkOff() } catch (e) { console.log("POWER_FAILED " + e); Qt.quit() } } }
  Timer { interval: 3300; running: true; onTriggered: { try { tester.checkLater() } catch (e) { console.log("POWER_FAILED " + e); Qt.quit() } } }
  Timer { interval: 8000; running: true; onTriggered: Qt.quit() }
}
