import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "com.aywuyan.github-release-updater"

  property int updateCount: 0
  property int checkedCount: 0
  property string checkState: "checking"
  property string message: "Checking GitHub Releases"

  readonly property bool isChinese: {
    var locale = String(Quickshell.env("LC_ALL") || Quickshell.env("LC_MESSAGES") || Quickshell.env("LANG") || "")
    return locale.toLowerCase().indexOf("zh") === 0
  }

  readonly property string pluginDir: {
    var url = String(Qt.resolvedUrl("."))
    if (url.indexOf("file://") !== 0) return ""
    var path = ""
    try { path = decodeURIComponent(url.substring(7)) } catch (e) { return "" }
    if (path.charAt(0) !== "/" || /[\x00-\x1f\x7f]/.test(path)) return ""
    return path.charAt(path.length - 1) === "/" ? path : path + "/"
  }

  function refresh() {
    if (pluginDir && !checkProc.running) checkProc.running = true
  }

  function tr(english, chinese) { return isChinese ? chinese : english }

  function applyResults(raw) {
    try {
      var result = JSON.parse(String(raw || "{}"))
      updateCount = Number(result.updates || 0)
      checkedCount = Number(result.checked || 0)
      if (result.errors && result.errors.length > 0) {
        checkState = "error"
        var error = String(result.errors[0].error || "")
        message = error.indexOf("rate limit") >= 0
          ? tr("GitHub API rate limit reached. Add a token in Settings.", "GitHub API 请求次数已达限制，请在设置中添加 Token。")
          : error || tr("GitHub check failed", "GitHub 检查失败")
      } else if (checkedCount === 0) {
        checkState = "setup"
        message = tr("No apps configured. Add one in the GitHub app panel.", "尚未登记应用，请打开 GitHub 应用面板添加。")
      } else if (updateCount > 0) {
        checkState = "updates"
        message = isChinese ? updateCount + " 个 GitHub 更新可用"
          : updateCount + " GitHub update" + (updateCount === 1 ? "" : "s") + " available"
      } else {
        checkState = "ready"
        message = tr("Configured GitHub apps are up to date", "已登记的 GitHub 应用均为最新版本")
      }
    } catch (e) {
      checkState = "error"
      message = tr("Could not read GitHub update status", "无法读取 GitHub 更新状态")
      updateCount = 0
    }
  }

  function openPanel() {
    if (root.bar && root.bar.shell)
      root.bar.shell.summon(root.moduleName, "{}")
  }

  visible: true
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  Process {
    id: checkProc
    command: ["python", root.pluginDir + "scripts/github_release_updater.py", "check", "--json"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.applyResults(text)
    }
    onExited: function(exitCode) {
      if (exitCode !== 0) {
        root.checkState = "error"
        root.message = "GitHub release check failed"
      }
    }
  }

  Timer {
    interval: 21600000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.checkState === "updates" ? "\uf019"
      : root.checkState === "error" || root.checkState === "setup" ? "\uf071" : "\uf021"
    slotSize: Style.bar.statusSlot
    fontSize: Style.font.caption
    tooltipText: root.message + "\n" + root.tr("Click to manage GitHub apps", "点击管理 GitHub 应用")
    onPressed: {
      root.openPanel()
    }
  }
}
