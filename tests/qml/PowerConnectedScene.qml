import QtQuick
import QtTest
import Quickshell

ShellRoot {
  WaxService {
    id: service
    settings: ({ globalKeys: false })
    property int launches: 0
    function startBridgeUnit() { launches++ }
    function syncKeys() {}
    Component.onCompleted: adoptSettings({ globalKeys: false })
  }
  TestCase {
    id: tester
    name: "ConnectedPowerOff"
    when: false
    function stop() {
      verify(service.bridgeUp)
      service.stopEngine()
      verify(service.closed)
    }
    function confirm() {
      verify(!service.bridgeUp, "the bridge socket stayed connected after power-off")
      verify(service.poweredOff)
      compare(service.launches, 0, "the stopped bridge was restarted")
      console.log("CONNECTED_POWER_OK")
      Qt.quit()
    }
  }
  Timer {
    interval: 50; repeat: true; running: true
    onTriggered: if (service.bridgeUp) {
      stop()
      try { tester.stop(); check.start() } catch (e) { console.log("POWER_FAILED " + e); Qt.quit() }
    }
  }
  Timer { id: check; interval: 3000; onTriggered: { try { tester.confirm() } catch (e) { console.log("POWER_FAILED " + e); Qt.quit() } } }
  Timer { interval: 8000; running: true; onTriggered: Qt.quit() }
}
