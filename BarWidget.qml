import QtQuick
import qs.Commons
import qs.Ui

// The bar entry point. The panel owns every process, the timer, and the state
// so a closed panel still keeps the bar icon current; this file only draws the
// icon and stands in for the panel in the bar's open/close routing
// (Bar.findPanelWidget needs open/close/opened on the bar-widget root).
BarWidget {
  id: root
  moduleName: "com.aywuyan.github-release-updater"

  readonly property var panel: panelLoader.item
  readonly property bool opened: panel ? panel.opened === true : false
  readonly property int updateCount: panel ? panel.updateCount : 0
  readonly property string state: panel ? panel.state : "idle"
  readonly property string tooltip: panel ? panel.message : ""

  function open() {
    if (panel) panel.open()
  }

  function close() {
    if (panel) panel.close()
  }

  function toggle() {
    opened ? close() : open()
  }

  function refresh() {
    if (panel) panel.refresh()
  }

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("anchorItem" in target) target.anchorItem = button
    if ("bar" in target) target.bar = root.bar
    if ("hostWidget" in target) target.hostWidget = root
  }

  onBarChanged: injectPanel()

  visible: true
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  Loader {
    id: panelLoader
    active: true
    visible: false
    source: Qt.resolvedUrl("Panel.qml")
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.state === "updates" ? "\uf019"
      : root.state === "error" ? "\uf071"
      : root.state === "setup" ? "\uf013"
      : "\uf021"
    slotSize: Style.bar.statusSlot
    fontSize: Style.font.caption
    tooltipText: root.tooltip

    onPressed: function(b) {
      if (b === Qt.MiddleButton) root.refresh()
      else if (b === Qt.RightButton) root.open()
      else root.toggle()
    }
  }
}
