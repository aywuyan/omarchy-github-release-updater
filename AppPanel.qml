import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "com.aywuyan.github-release-updater"
  ipcTarget: moduleName

  property var apps: []
  property string statusText: ""
  property string checkOutput: ""
  property string checkError: ""
  property int checkExitCode: -1
  property bool checkOutputFinished: false
  property bool checkErrorFinished: false
  property string addFeedback: ""
  property bool adding: false
  property string addOutput: ""
  property string addError: ""
  property int addExitCode: -1
  property bool addStreamFinished: false
  property bool addErrorStreamFinished: false
  property string pendingRemoveName: ""
  property string removeOutput: ""
  property string removeError: ""
  property int removeExitCode: -1
  property bool removeStreamFinished: false
  property bool removeErrorStreamFinished: false
  property bool tokenSaved: false
  property bool environmentTokenAvailable: false
  property bool ghAvailable: false
  property bool ghAuthenticated: false
  property bool refreshingLoginStatus: false
  property bool proxySaved: false
  property bool proxyEnabled: false
  property bool proxyTesting: false
  property bool detectingVersion: false
  property bool addAfterVersionDetection: false
  property string settingsStatus: ""
  property string pendingSettings: ""
  property string languagePreference: "auto"

  readonly property bool isChinese: {
    if (languagePreference === "zh") return true
    if (languagePreference === "en") return false
    var locale = String(Quickshell.env("LC_ALL") || Quickshell.env("LC_MESSAGES") || Quickshell.env("LANG") || "")
    return locale.toLowerCase().indexOf("zh") === 0
  }

  readonly property string helperPath: {
    var url = String(Qt.resolvedUrl("."))
    if (url.indexOf("file://") !== 0) return ""
    var path = ""
    try { path = decodeURIComponent(url.substring(7)) } catch (e) { return "" }
    if (path.charAt(0) !== "/" || /[\x00-\x1f\x7f]/.test(path)) return ""
    return (path.charAt(path.length - 1) === "/" ? path : path + "/") + "scripts/github_release_updater.py"
  }

  function tr(english, chinese) { return isChinese ? chinese : english }

  function isErrorText(value) {
    var message = String(value || "").toLowerCase()
    return message.indexOf("error") >= 0 || message.indexOf("failed") >= 0
      || message.indexOf("could not") >= 0 || message.indexOf("cannot") >= 0
      || message.indexOf("rate limit") >= 0
      || message.indexOf("enter ") === 0
      || String(value || "").indexOf("失败") >= 0 || String(value || "").indexOf("无法") >= 0
      || String(value || "").indexOf("未安装") >= 0 || String(value || "").indexOf("请填写") === 0
      || String(value || "").indexOf("请") === 0 || String(value || "").indexOf("限流") >= 0
  }

  function statusLabel(status) {
    if (status === "update available") return tr("Update available", "有可用更新")
    if (status === "up to date") return tr("Up to date", "已是最新")
    if (status === "installed version is newer") return tr("Installed version is newer", "已安装版本较新")
    if (status === "check failed") return tr("Check failed", "检查失败")
    return tr("Compare manually", "请手动比较")
  }

  function dismiss() {
    if (shell && typeof shell.hide === "function") shell.hide(moduleName)
    else close()
  }

  function refresh() {
    if (!helperPath) {
      statusText = tr("Could not locate the plugin helper.", "找不到插件辅助程序。")
      return
    }
    if (checkProc.running) {
      statusText = tr("A GitHub check is already running…", "GitHub 检查正在进行…")
      return
    }
    checkOutput = ""
    checkError = ""
    checkExitCode = -1
    checkOutputFinished = false
    checkErrorFinished = false
    statusText = tr("Checking registered apps on GitHub…", "正在检查已登记应用的 GitHub Release…")
    checkProc.running = true
  }

  function finishCheckIfReady() {
    if (checkExitCode < 0 || !checkOutputFinished || !checkErrorFinished) return
    if (checkOutput.trim() !== "") {
      readResults(checkOutput)
    } else {
      statusText = checkError.trim() || tr("GitHub check could not start.", "无法启动 GitHub 检查。")
    }
    if (checkExitCode !== 0 && checkOutput.trim() === "" && checkError.trim() === "")
      statusText = tr("GitHub check failed without an error message.", "GitHub 检查失败，但没有返回错误信息。")
  }

  function readResults(raw) {
    try {
      var result = JSON.parse(String(raw || "{}"))
      apps = result.apps || []
      var errors = result.errors || []
      var errorText = errors.length > 0 ? String(errors[0].error || "") : ""
      statusText = errorText !== "" ? (errorText.indexOf("rate limit") >= 0
          ? tr("GitHub API rate limit reached. Add a token in Settings.", "GitHub API 请求次数已达限制，请在设置中添加 Token。")
          : errorText)
        : apps.length === 0 ? tr("No repositories registered yet. Add one below.", "还没有登记仓库，请在下方添加。")
        : isChinese ? result.updates + " 个更新，已检查 " + apps.length + " 个应用。"
        : result.updates + " update" + (result.updates === 1 ? "" : "s") + " available across " + apps.length + " app" + (apps.length === 1 ? "" : "s") + "."
    } catch (e) {
      statusText = tr("Could not read the GitHub check result.", "无法读取 GitHub 检查结果。")
    }
  }

  function addApp() {
    if (adding || addProc.running || detectingVersion) return
    if (!helperPath) {
      addFeedback = tr("Could not locate the plugin helper.", "找不到插件辅助程序。")
      return
    }
    if (releaseUrl.text.trim() === "") {
      addFeedback = tr("Enter a GitHub repository or Release URL.", "请填写 GitHub 仓库或 Release 地址。")
      return
    }
    if (installedVersion.text.trim() === "") {
      detectCurrentVersion(true)
      return
    }
    if (installMethod.currentText === "appimage" && appTarget.text.trim() === "") {
      addFeedback = tr("Enter the absolute path where the AppImage is installed.", "请填写 AppImage 的绝对安装路径。")
      return
    }
    addFeedback = tr("Saving app to the local tracking list…", "正在保存到本地跟踪列表…")
    addOutput = ""
    addError = ""
    addExitCode = -1
    addStreamFinished = false
    addErrorStreamFinished = false
    addProc.command = [
      "python", helperPath, "add",
      "--url", releaseUrl.text.trim(),
      "--version", installedVersion.text.trim(),
      "--asset-regex", assetPattern.text.trim(),
      "--install-method", installMethod.currentText,
      "--release-channel", releaseChannel.currentText,
      "--name", appName.text.trim(),
      "--target", appTarget.text.trim(),
      "--json"
    ]
    adding = true
    addProc.running = true
  }

  function finishAddIfReady() {
    if (adding && addStreamFinished && addErrorStreamFinished && addExitCode >= 0)
      handleAdded(addOutput, addExitCode)
  }

  function handleAdded(raw, exitCode) {
    adding = false
    try {
      var result = JSON.parse(String(raw || "{}"))
      if (exitCode !== 0 || !result.ok) {
        addFeedback = String(result.error || tr("Could not add this repository.", "无法添加此仓库。"))
        return
      }
      addFeedback = tr("Added ", "已登记 ") + result.name + tr(" to local tracking. GitHub checks run afterward; a token is only needed if the API is rate limited.", "到本地跟踪列表。之后才会检查 GitHub；只有 API 限流时才需要 Token。")
      releaseUrl.text = ""
      installedVersion.text = ""
      assetPattern.text = ""
      releaseChannel.currentIndex = 0
      appName.text = ""
      appTarget.text = ""
      Qt.callLater(root.refresh)
    } catch (e) {
      addFeedback = addError.trim() !== "" ? addError.trim()
        : tr("Could not add this repository. Check the URL and fields.", "无法添加仓库，请检查地址和填写内容。")
    }
  }

  function sendSettings(payload) {
    var line = JSON.stringify(payload) + "\n"
    if (!settingsProc.running) {
      pendingSettings = line
      settingsProc.running = true
    } else {
      settingsProc.write(line)
    }
  }

  function handleSettingsLine(line) {
    try {
      var result = JSON.parse(String(line || "{}"))
      if (result.action === "test-proxy") {
        proxyTesting = false
        settingsStatus = result.ok
          ? tr("Proxy works (GitHub HTTP %1).", "代理可用（GitHub HTTP %1）。").arg(result.http_status)
          : tr("Proxy test failed: ", "代理测试失败：") + String(result.error || "")
        return
      }
      if (result.token_saved !== undefined) tokenSaved = result.token_saved === true
      if (result.environment_token_available !== undefined) environmentTokenAvailable = result.environment_token_available === true
      if (result.gh_available !== undefined) ghAvailable = result.gh_available === true
      if (result.gh_authenticated !== undefined) ghAuthenticated = result.gh_authenticated === true
      if (result.proxy_saved !== undefined) proxySaved = result.proxy_saved === true
      if (result.proxy_address !== undefined) localProxyField.text = result.proxy_address
      if (result.proxy_enabled !== undefined) {
        proxyEnabled = result.proxy_enabled === true
        proxyToggle.checked = proxyEnabled
      }
      if (result.language !== undefined) {
        languagePreference = result.language
      }
      if (!result.ok) {
        settingsStatus = String(result.error || tr("Could not save settings.", "无法保存设置。"))
      } else if (result.action === "save-token") {
        tokenSaved = true
        settingsStatus = tr("Token saved in the desktop keyring.", "Token 已保存到桌面密钥环。")
      } else if (result.action === "clear-token") {
        tokenSaved = false
        settingsStatus = tr("Saved token removed.", "已删除保存的 Token。")
      } else if (result.action === "save-proxy") {
        settingsStatus = tr("Proxy settings saved.", "代理设置已保存。")
      } else if (result.action === "set-proxy-enabled") {
        proxyEnabled = result.proxy_enabled === true
        proxyToggle.checked = proxyEnabled
        settingsStatus = proxyEnabled ? tr("Local proxy enabled.", "本地代理已启用。") : tr("Local proxy disabled.", "本地代理已停用。")
      } else if (result.action === "clear-proxy") {
        settingsStatus = tr("Saved proxy settings removed.", "已清除保存的代理设置。")
      } else if (result.action === "save-language") {
        languagePreference = result.language || "auto"
        settingsStatus = tr("Display language saved.", "显示语言已保存。")
      } else if (result.action === "get") {
        var wasRefreshingLoginStatus = refreshingLoginStatus
        refreshingLoginStatus = false
        settingsStatus = wasRefreshingLoginStatus ? tr("Login status refreshed.", "登录状态已刷新。") : ""
      }
    } catch (e) {
      settingsStatus = tr("Could not read settings response.", "无法读取设置响应。")
    }
  }

  function saveToken() {
    if (tokenField.text === "") return
    sendSettings({action: "save-token", token: tokenField.text})
    tokenField.text = ""
    settingsStatus = tr("Saving token to the desktop keyring…", "正在将 Token 保存到桌面密钥环…")
  }

  function clearToken() {
    sendSettings({action: "clear-token"})
    settingsStatus = tr("Removing saved token…", "正在删除已保存的 Token…")
  }

  function startGhLogin() {
    if (!ghAvailable) {
      settingsStatus = tr("GitHub CLI (gh) is not installed.", "未安装 GitHub CLI（gh）。")
      return
    }
    settingsStatus = tr("Complete gh login in the terminal, then refresh status here.", "请在终端完成 gh 登录，然后在此刷新状态。")
    Quickshell.execDetached(["omarchy-launch-floating-terminal-with-presentation", "gh auth login"])
  }

  function refreshLoginStatus() {
    refreshingLoginStatus = true
    settingsStatus = tr("Refreshing GitHub login status…", "正在刷新 GitHub 登录状态…")
    sendSettings({action: "get"})
  }

  function saveProxy() {
    sendSettings({
      action: "save-proxy",
      proxy_address: localProxyField.text,
      proxy_enabled: proxyToggle.checked
    })
    settingsStatus = tr("Saving proxy settings…", "正在保存代理设置…")
  }

  function setProxyEnabled(enabled) {
    proxyEnabled = enabled
    if (!proxySaved && enabled && localProxyField.text.trim() !== "") {
      saveProxy()
      return
    }
    sendSettings({action: "set-proxy-enabled", enabled: enabled})
    settingsStatus = tr("Updating proxy state…", "正在更新代理状态…")
  }

  function testProxy() {
    proxyTesting = true
    settingsStatus = tr("Testing local proxy…", "正在测试本地代理…")
    sendSettings({action: "test-proxy", proxy_address: localProxyField.text.trim()})
  }

  function clearProxy() {
    sendSettings({action: "clear-proxy"})
    localProxyField.text = ""
    proxyEnabled = false
    proxyToggle.checked = false
    settingsStatus = tr("Removing saved proxy settings…", "正在删除已保存的代理设置…")
  }

  function saveLanguage(language) {
    languagePreference = language
    sendSettings({action: "save-language", language: language})
    settingsStatus = tr("Saving display language…", "正在保存显示语言…")
  }

  function detectCurrentVersion(forAdd) {
    if (!helperPath) {
      if (forAdd) addFeedback = tr("Could not locate the plugin helper.", "找不到插件辅助程序。")
      else statusText = tr("Could not locate the plugin helper.", "找不到插件辅助程序。")
      return
    }
    if (detectingVersion || detectVersionProc.running) {
      if (forAdd) addFeedback = tr("Version detection is already running…", "版本检测正在进行…")
      else statusText = tr("Version detection is already running…", "版本检测正在进行…")
      return
    }
    if (installMethod.currentText === "appimage" && appTarget.text.trim() === "") {
      if (forAdd) addFeedback = tr("Enter the AppImage path before adding.", "添加前请填写 AppImage 安装路径。")
      else statusText = tr("Enter the AppImage path before detecting its version.", "请先填写 AppImage 路径再检测版本。")
      return
    }
    if (installMethod.currentText === "pacman" && appName.text.trim() === "" && releaseUrl.text.trim() === "") {
      if (forAdd) addFeedback = tr("Enter a package name or repository URL before adding.", "添加前请填写包名或仓库地址。")
      else statusText = tr("Enter a package name or repository URL before detecting.", "请先填写包名或仓库地址再检测。")
      return
    }
    detectingVersion = true
    addAfterVersionDetection = forAdd === true
    if (addAfterVersionDetection) addFeedback = tr("Detecting the installed version before adding…", "正在自动检测当前版本…")
    else statusText = tr("Detecting installed version…", "正在检测已安装版本…")
    detectVersionProc.command = [
      "python", helperPath, "detect-version", appName.text.trim(),
      "--install-method", installMethod.currentText,
      "--target", appTarget.text.trim(),
      "--url", releaseUrl.text.trim(), "--json"
    ]
    detectVersionProc.running = true
  }

  function handleDetectedVersion(raw) {
    detectingVersion = false
    var shouldContinueAdd = addAfterVersionDetection
    addAfterVersionDetection = false
    try {
      var result = JSON.parse(String(raw || "{}"))
      if (!result.ok) {
        if (shouldContinueAdd)
          addFeedback = tr("Could not detect the installed version. Enter it manually, then click Add app.", "无法自动检测当前版本，请手动填写版本后再次点击“添加应用”。")
        else statusText = String(result.error || tr("Could not detect the installed version.", "无法检测已安装版本。"))
        return
      }
      installedVersion.text = result.version
      if (shouldContinueAdd) {
        addFeedback = tr("Detected ", "已检测到 ") + result.version + tr("; adding app…", "；正在登记应用…")
        Qt.callLater(root.addApp)
      } else {
        statusText = tr("Detected ", "已检测到 ") + result.version + tr(" from ", "，来源：") + result.source
      }
    } catch (e) {
      if (shouldContinueAdd)
        addFeedback = tr("Could not detect the installed version. Enter it manually, then click Add app.", "无法自动检测当前版本，请手动填写版本后再次点击“添加应用”。")
      else statusText = tr("Could not read the version detection result.", "无法读取版本检测结果。")
    }
  }

  function confirmRemove(name) {
    pendingRemoveName = name
    removeDialog.open()
  }

  function removeApp(name) {
    if (!helperPath) {
      statusText = tr("Could not locate the plugin helper.", "找不到插件辅助程序。")
      return
    }
    if (removeProc.running) {
      statusText = tr("An app removal is already running…", "应用移除操作正在进行…")
      return
    }
    removeOutput = ""
    removeError = ""
    removeExitCode = -1
    removeStreamFinished = false
    removeErrorStreamFinished = false
    statusText = tr("Removing ", "正在移除 ") + name + tr(" from tracking…", "的跟踪登记…")
    removeProc.command = ["python", helperPath, "remove", name, "--json"]
    removeProc.running = true
  }

  function finishRemoveIfReady() {
    if (!removeStreamFinished || !removeErrorStreamFinished || removeExitCode < 0) return
    try {
      var result = JSON.parse(String(removeOutput || "{}"))
      if (removeExitCode !== 0 || !result.ok) {
        statusText = String(result.error || removeError || tr("Could not remove this app.", "无法移除此应用。"))
      } else {
        statusText = tr("Removed ", "已移除 ") + result.name + tr(" from tracking.", "的更新跟踪。")
        apps = apps.filter(function(app) { return app.name !== result.name })
      }
    } catch (e) {
      statusText = removeError.trim() || tr("Could not read the remove result.", "无法读取移除结果。")
    }
  }

  function launchUpdate(name) {
    if (!helperPath) {
      statusText = tr("Could not locate the plugin helper.", "找不到插件辅助程序。")
      return
    }
    var command = "python " + Util.shellQuote(helperPath) + " update " + Util.shellQuote(name) + " --apply"
    Quickshell.execDetached(["omarchy-launch-floating-terminal-with-presentation", command])
    statusText = tr("Opening update terminal for ", "正在打开更新终端：") + name
  }

  function setAppChannel(name, channel) {
    if (!helperPath || channelProc.running) return
    statusText = tr("Switching ", "正在切换 ") + name + tr(" to ", "到") + channel + "…"
    channelProc.command = ["python", helperPath, "set-channel", name, "--release-channel", channel]
    channelProc.running = true
  }

  onOpenedChanged: if (opened) refresh()

  Process {
    id: checkProc
    command: ["python", root.helperPath, "check", "--json"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.checkOutput = String(text || "")
        root.checkOutputFinished = true
        root.finishCheckIfReady()
      }
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.checkError = String(text || "")
        root.checkErrorFinished = true
        root.finishCheckIfReady()
      }
    }
    onExited: function(exitCode) {
      root.checkExitCode = exitCode
      root.finishCheckIfReady()
    }
  }

  Process {
    id: channelProc
    command: []
    onExited: function(exitCode) {
      root.statusText = exitCode === 0
        ? root.tr("Release channel updated.", "发行通道已更新。")
        : root.tr("Could not update the release channel.", "无法更新发行通道。")
      root.refresh()
    }
  }

  Process {
    id: settingsProc
    command: ["python", root.helperPath, "settings-service"]
    stdinEnabled: true
    stdout: SplitParser { onRead: function(line) { root.handleSettingsLine(line) } }
    onStarted: {
      if (root.pendingSettings !== "") {
        settingsProc.write(root.pendingSettings)
        root.pendingSettings = ""
      }
    }
  }

  Process {
    id: addProc
    command: []
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.addOutput = String(text || "")
        root.addStreamFinished = true
        root.finishAddIfReady()
      }
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.addError = String(text || "")
        root.addErrorStreamFinished = true
        root.finishAddIfReady()
      }
    }
    onExited: function(exitCode) {
      root.addExitCode = exitCode
      root.finishAddIfReady()
    }
  }

  Process {
    id: detectVersionProc
    command: []
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.handleDetectedVersion(String(text || ""))
    }
    stderr: StdioCollector { waitForEnd: true }
    onExited: function(exitCode) {
      if (exitCode !== 0 && root.detectingVersion)
        root.handleDetectedVersion('{"ok":false,"error":"version detection failed"}')
    }
  }

  Process {
    id: removeProc
    command: []
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.removeOutput = String(text || "")
        root.removeStreamFinished = true
        root.finishRemoveIfReady()
      }
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.removeError = String(text || "")
        root.removeErrorStreamFinished = true
        root.finishRemoveIfReady()
      }
    }
    onExited: function(exitCode) {
      root.removeExitCode = exitCode
      root.finishRemoveIfReady()
    }
  }

  PanelWindow {
    visible: root.opened
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "github-release-updater"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive

    Rectangle {
      anchors.fill: parent
      color: Qt.rgba(0, 0, 0, 0.74)
      MouseArea { anchors.fill: parent; onClicked: root.dismiss() }
    }

    Item {
      id: keyCatcher
      anchors.fill: parent
      focus: root.opened
      Keys.onEscapePressed: root.dismiss()

      Rectangle {
        id: card
        anchors.centerIn: parent
        width: Math.min(parent.width - Style.space(40), Style.space(900))
        height: Math.min(parent.height - Style.space(40), Style.space(760))
        radius: Style.cornerRadius
        color: "#17191d"
        border.color: "#353941"
        border.width: 1

        MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons }

        ColumnLayout {
          anchors.fill: parent
          anchors.margins: Style.space(24)
          spacing: Style.space(12)

          RowLayout {
            Layout.fillWidth: true
            Text {
              Layout.fillWidth: true
              text: root.tr("GitHub application updates", "GitHub 应用更新")
              color: "#f1f3f5"
              font.family: Style.font.family
              font.pixelSize: Style.font.title
              font.bold: true
            }
            Button { text: root.tr("Settings", "设置"); onClicked: settingsPopup.open() }
            Button { text: root.tr("Close", "关闭"); onClicked: root.dismiss() }
          }

          Text {
            Layout.fillWidth: true
            text: root.tr("Track GitHub Releases for apps outside your usual package sources. Add a repository or release URL, then choose the Linux asset and install format.", "跟踪包管理器未提供或更新滞后的应用。添加 GitHub 仓库或 Release 地址，然后选择 Linux 安装文件和安装方式。")
            color: "#b9bec8"
            wrapMode: Text.WordWrap
            font.family: Style.font.family
            font.pixelSize: Style.font.body
          }

          RowLayout {
            Layout.fillWidth: true
            Text {
              Layout.fillWidth: true
              text: root.statusText
              color: root.isErrorText(root.statusText) ? "#ff8585" : "#c4cad4"
              wrapMode: Text.WordWrap
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
            Button { text: checkProc.running ? root.tr("Checking…", "检查中…") : root.tr("Check now", "立即检查"); enabled: !checkProc.running; onClicked: root.refresh() }
          }

          Rectangle { Layout.fillWidth: true; height: 1; color: "#353941" }

          Text {
            text: root.tr("Registered apps", "已登记应用")
            color: "#f1f3f5"
            font.family: Style.font.family
            font.pixelSize: Style.font.body
            font.bold: true
          }

          ListView {
            id: appList
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(contentHeight, Style.space(190))
            clip: true
            spacing: Style.space(6)
            model: root.apps
            delegate: Rectangle {
              required property var modelData
              width: appList.width
              height: Style.space(68)
              radius: Math.max(2, Style.cornerRadius * 0.65)
              color: "#22252b"

              RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Style.space(12)
                anchors.rightMargin: Style.space(10)
                spacing: Style.space(8)

                ColumnLayout {
                  Layout.fillWidth: true
                  spacing: Style.space(2)
                  Text {
                    Layout.fillWidth: true
                    text: modelData.name + "  " + modelData.current + " → " + modelData.latest
                      + (modelData.release_channel === "development" ? " · " + root.tr("dev", "开发版") : "")
                    color: "#f1f3f5"
                    font.family: Style.font.family
                    font.pixelSize: Style.font.body
                    elide: Text.ElideRight
                  }
                  Text {
                    Layout.fillWidth: true
                    text: modelData.repo + " · " + modelData.asset + " · " + root.statusLabel(modelData.status)
                    color: modelData.status === "update available" ? "#f5c26b" : "#a6adba"
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    elide: Text.ElideRight
                  }
                }

                Button {
                  visible: modelData.status === "update available"
                  text: root.tr("Update…", "更新…")
                  onClicked: root.launchUpdate(modelData.name)
                }
                Button {
                  text: modelData.release_channel === "development"
                    ? root.tr("Switch to stable", "切换到稳定版")
                    : root.tr("Switch to dev", "切换到开发版")
                  enabled: !channelProc.running
                  onClicked: root.setAppChannel(modelData.name, modelData.release_channel === "development" ? "stable" : "development")
                }
                Button {
                  text: root.tr("Release", "发行页")
                  onClicked: {
                    root.statusText = root.tr("Opening release page for ", "正在打开发行页：") + modelData.name
                    Quickshell.execDetached(["xdg-open", modelData.release_url])
                  }
                }
                Button {
                  text: root.tr("Remove", "移除")
                  onClicked: root.confirmRemove(modelData.name)
                }
              }
            }
          }

          Rectangle { Layout.fillWidth: true; height: 1; color: "#353941" }

          Text {
            text: root.tr("Add a GitHub repository or Release URL", "添加 GitHub 仓库或 Release 地址")
            color: "#f1f3f5"
            font.family: Style.font.family
            font.pixelSize: Style.font.body
            font.bold: true
          }

          TextField {
            id: releaseUrl
            Layout.fillWidth: true
            placeholderText: root.tr("https://github.com/owner/repository/releases/latest", "粘贴仓库地址或 /releases/tag/... 地址")
            selectByMouse: true
          }

          RowLayout {
            Layout.fillWidth: true
            TextField { id: appName; Layout.preferredWidth: 180; placeholderText: root.tr("App / package name", "应用名 / Pacman 包名"); selectByMouse: true }
            TextField { id: installedVersion; Layout.preferredWidth: 180; placeholderText: root.tr("Installed version", "当前安装版本"); selectByMouse: true }
            ComboBox { id: installMethod; Layout.preferredWidth: 140; model: ["pacman", "appimage"] }
            ComboBox { id: releaseChannel; Layout.preferredWidth: 150; model: ["stable", "development"] }
            Item { Layout.fillWidth: true }
          }

          TextField {
            id: assetPattern
            Layout.fillWidth: true
            placeholderText: root.tr("Optional asset filename regex override (blank = auto-detect)", "可选：文件名匹配正则（留空自动检测）")
            selectByMouse: true
          }

          TextField {
            id: appTarget
            Layout.fillWidth: true
            visible: installMethod.currentText === "appimage"
            placeholderText: root.tr("Absolute AppImage path, e.g. ~/Applications/App.AppImage", "AppImage 安装路径，例如 ~/Applications/App.AppImage")
            selectByMouse: true
          }

          RowLayout {
            Layout.fillWidth: true
            Text {
              Layout.fillWidth: true
              text: root.tr("Adding saves locally. GitHub checks run afterward; a token is only needed if the API is rate limited.", "添加应用只保存到本地，随后才检查 GitHub；只有 API 限流时才需要 Token。")
              color: "#a6adba"
              wrapMode: Text.WordWrap
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
            Button {
              text: root.detectingVersion ? root.tr("Detecting version…", "检测版本中…")
                : root.adding ? root.tr("Adding…", "正在添加…") : root.tr("Add app", "添加应用")
              enabled: !root.adding && !root.detectingVersion
              onClicked: root.addApp()
            }
          }

          Text {
            Layout.fillWidth: true
            visible: root.addFeedback !== ""
            text: root.addFeedback
            color: root.isErrorText(root.addFeedback) ? "#ff8585" : "#c4cad4"
            wrapMode: Text.WordWrap
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
        }
      }

      Popup {
        id: settingsPopup
        anchors.centerIn: Overlay.overlay
        width: Math.min(parent.width - Style.space(48), Style.space(560))
        height: Math.min(parent.height - Style.space(48), Style.space(590))
        modal: true
        focus: true
        padding: Style.space(20)
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        onOpened: {
          root.settingsStatus = root.tr("Loading settings…", "正在加载设置…")
          root.sendSettings({action: "get"})
        }
        background: Rectangle {
          radius: Style.cornerRadius
          color: "#17191d"
          border.color: "#454a54"
          border.width: 1
        }
        contentItem: ColumnLayout {
          spacing: Style.space(12)

          RowLayout {
            Layout.fillWidth: true
            Text {
              Layout.fillWidth: true
              text: root.tr("GitHub and proxy settings", "GitHub 与代理设置")
              color: "#f1f3f5"
              font.family: Style.font.family
              font.pixelSize: Style.font.title
              font.bold: true
            }
            Button { text: root.tr("Close", "关闭"); onClicked: settingsPopup.close() }
          }

          Text {
            Layout.fillWidth: true
            text: root.environmentTokenAvailable
              ? root.tr("Using GH_TOKEN/GITHUB_TOKEN from the environment.", "正在使用环境变量中的 GH_TOKEN/GITHUB_TOKEN。")
              : root.tokenSaved
                ? root.tr("A GitHub token is saved in the desktop keyring.", "GitHub Token 已保存在桌面密钥环中。")
                : root.ghAuthenticated
                  ? root.tr("GitHub CLI is signed in.", "GitHub CLI 已登录。")
                  : root.tr("No GitHub token is available. Sign in with gh or save a token.", "当前没有可用的 GitHub Token；请使用 gh 登录或保存 Token。")
            color: "#b9bec8"
            wrapMode: Text.WordWrap
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }

          TextField {
            id: tokenField
            Layout.fillWidth: true
            echoMode: TextInput.Password
            placeholderText: root.tr("Paste a GitHub token", "粘贴 GitHub Token")
            selectByMouse: true
          }

          RowLayout {
            Layout.fillWidth: true
            Button { text: root.tr("Save token", "保存 Token"); enabled: tokenField.text !== ""; onClicked: root.saveToken() }
            Button { text: root.tr("Remove saved token", "删除已保存 Token"); enabled: root.tokenSaved; onClicked: root.clearToken() }
            Button { text: root.tr("Sign in with gh", "使用 gh 登录"); onClicked: root.startGhLogin() }
            Button { text: root.tr("Refresh status", "刷新状态"); onClicked: root.refreshLoginStatus() }
          }

          Text {
            Layout.fillWidth: true
            text: root.tr("Display language", "显示语言")
            color: "#f1f3f5"
            font.family: Style.font.family
            font.pixelSize: Style.font.body
          }
          RowLayout {
            Layout.fillWidth: true
            spacing: Style.space(6)
            Button {
              Layout.fillWidth: true
              text: (root.languagePreference === "auto" ? "✓ " : "") + root.tr("Automatic", "自动")
              onClicked: root.saveLanguage("auto")
            }
            Button {
              Layout.fillWidth: true
              text: (root.languagePreference === "zh" ? "✓ " : "") + "简体中文"
              onClicked: root.saveLanguage("zh")
            }
            Button {
              Layout.fillWidth: true
              text: (root.languagePreference === "en" ? "✓ " : "") + "English"
              onClicked: root.saveLanguage("en")
            }
          }

          Rectangle { Layout.fillWidth: true; height: 1; color: "#353941" }

          Text {
            Layout.fillWidth: true
            text: root.proxySaved
              ? root.tr("Saved local address is shown below. When disabled, system proxy settings are used.", "已保存的本地地址显示在下方。停用时使用系统代理设置。")
              : root.tr("Enter a local proxy address. Supports 127.0.0.1:PORT or 0:PORT.", "填写本地代理地址，支持 127.0.0.1:端口 或 0:端口。")
            color: "#b9bec8"
            wrapMode: Text.WordWrap
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }

          TextField {
            id: localProxyField
            Layout.fillWidth: true
            placeholderText: "127.0.0.1:7890"
            selectByMouse: true
          }

          CheckBox {
            id: proxyToggle
            text: root.tr("Enable local proxy", "启用本地代理")
            enabled: root.proxySaved || localProxyField.text.trim() !== ""
            onClicked: root.setProxyEnabled(checked)
          }

          RowLayout {
            Layout.fillWidth: true
            Button { text: root.tr("Save proxy", "保存代理"); onClicked: root.saveProxy() }
            Button { text: root.tr("Clear saved proxy", "清除已保存代理"); enabled: root.proxySaved; onClicked: root.clearProxy() }
            Button {
              text: root.proxyTesting ? root.tr("Testing…", "测试中…") : root.tr("Test proxy", "测试代理")
              enabled: !root.proxyTesting && localProxyField.text.trim() !== ""
              onClicked: root.testProxy()
            }
            Item { Layout.fillWidth: true }
          }

          Text {
            Layout.fillWidth: true
            text: root.settingsStatus
            color: root.isErrorText(root.settingsStatus) ? "#ff8585" : "#c4cad4"
            wrapMode: Text.WordWrap
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }

          Item { Layout.fillHeight: true }
        }
      }

      Popup {
        id: removeDialog
        anchors.centerIn: Overlay.overlay
        width: Math.min(parent.width - Style.space(48), Style.space(500))
        modal: true
        focus: true
        padding: Style.space(20)
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle {
          radius: Style.cornerRadius
          color: "#17191d"
          border.color: "#454a54"
          border.width: 1
        }
        contentItem: ColumnLayout {
          spacing: Style.space(12)
          Text {
            Layout.fillWidth: true
            text: root.tr("Remove app from tracking?", "从跟踪列表中移除此应用？")
            color: "#f1f3f5"
            font.family: Style.font.family
            font.pixelSize: Style.font.title
            font.bold: true
          }
          Text {
            Layout.fillWidth: true
            text: root.tr("Remove ", "移除 ") + root.pendingRemoveName
              + root.tr(" from GitHub checks? This does not uninstall the app.", "的 GitHub 更新跟踪？这不会卸载应用。")
            color: "#b9bec8"
            wrapMode: Text.WordWrap
            font.family: Style.font.family
            font.pixelSize: Style.font.body
          }
          RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            Button { text: root.tr("Cancel", "取消"); onClicked: removeDialog.close() }
            Button {
              text: root.tr("Remove", "移除")
              onClicked: {
                removeDialog.close()
                root.removeApp(root.pendingRemoveName)
              }
            }
          }
        }
      }
    }
  }
}
