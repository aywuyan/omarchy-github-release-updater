import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// Bar popup for tracking GitHub releases. All processes, the timer, and the
// state live here so the bar icon stays correct while the card is closed; the
// bar widget only forwards open/close/refresh and draws the icon.
Panel {
  id: root
  moduleName: "com.aywuyan.github-release-updater"
  ipcTarget: moduleName
  // The plugin owns the one IPC handler for its target so it can also expose
  // refresh() next to the open/close/toggle the base panel provides.
  manageIpc: false

  property Item anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  // ---- check state -----------------------------------------------------
  property var apps: []
  property int checkedCount: 0
  property int updateCount: 0
  property int installCount: 0
  property string state: "idle"          // idle | checking | ready | updates | setup | error
  property string statusCode: ""
  property string statusDetail: ""
  property bool checking: false
  property bool checkActive: false
  property bool pendingRefresh: false
  property double lastCheckAt: 0

  // ---- page + cursor + notices ----------------------------------------
  property string page: "list"           // list | settings
  property int cursor: -1
  property string pendingRemove: ""
  property string noticeKey: ""
  property string noticeArg1: ""
  property string noticeArg2: ""
  property string noticeArg3: ""
  property string noticeCode: ""
  property string noticeDetail: ""
  property bool noticeError: false
  // "add" keeps form feedback next to the Add button instead of the status row.
  property string noticeArea: "list"

  // ---- add / remove / channel actions ---------------------------------
  property bool adding: false
  property bool detecting: false
  property bool removing: false
  property bool switchingChannel: false
  property bool addAfterDetect: false
  property bool addWithoutVersion: false
  property bool addingWithoutVersion: false

  // ---- form drafts -----------------------------------------------------
  property string formUrl: ""
  property string formName: ""
  property string formPackage: ""
  property string formVersion: ""
  property string formRegex: ""
  property string formTarget: ""
  property string formMethod: "auto"
  property string formChannel: "stable"

  // ---- settings --------------------------------------------------------
  property bool settingsLoaded: false
  property bool tokenSaved: false
  property bool environmentTokenAvailable: false
  property bool ghAvailable: false
  property bool ghAuthenticated: false
  property bool proxySaved: false
  property bool proxyEnabled: false
  property bool proxyTesting: false
  property string tokenDraft: ""
  property string proxyDraft: ""
  property string settingsStatusCode: ""
  property string settingsKey: ""
  property string settingsArg: ""
  property bool settingsError: false
  property string pendingSettings: ""

  readonly property string helperPath: {
    var url = String(Qt.resolvedUrl("."))
    if (url.indexOf("file://") !== 0) return ""
    var path = ""
    try { path = decodeURIComponent(url.substring(7)) } catch (e) { return "" }
    if (path.charAt(0) !== "/" || /[\x00-\x1f\x7f]/.test(path)) return ""
    return (path.charAt(path.length - 1) === "/" ? path : path + "/") + "scripts/github_release_updater.py"
  }

  readonly property string configHome: Quickshell.env("XDG_CONFIG_HOME") || (Quickshell.env("HOME") + "/.config")

  Commons.Translations {
    id: strings
    directory: Qt.resolvedUrl("i18n")
  }

  WatchedFile {
    id: settingsFile
    path: root.configHome + "/omarchy/github-release-updater/settings.json"
  }

  WatchedFile {
    id: manifestFile
    path: {
      var url = String(Qt.resolvedUrl("manifest.json"))
      if (url.indexOf("file://") !== 0) return ""
      try { return decodeURIComponent(url.substring(7)) } catch (e) { return "" }
    }
  }

  readonly property string pluginVersion: {
    try { return String(JSON.parse(String(manifestFile.text || "{}")).version || "") }
    catch (e) { return "" }
  }

  // The timer interval follows settings.json, so changing it in the panel
  // takes effect immediately without a shell restart.
  readonly property int configuredIntervalHours: {
    try {
      var data = JSON.parse(String(settingsFile.text || "{}"))
      var value = Number(data.check_interval_hours)
      if (!isFinite(value) || value < 0) return 6
      return Math.min(168, Math.round(value))
    } catch (e) {
      return 6
    }
  }
  readonly property bool autoCheckEnabled: configuredIntervalHours > 0

  // ---- localized text --------------------------------------------------
  function errorText(code) {
    if (!code) return ""
    return strings.t("errors." + code, "")
  }

  function statusText() {
    if (state === "error") {
      var known = errorText(statusCode)
      if (known !== "") return known
      return statusDetail !== "" ? statusDetail : strings.t("errors.unknown")
    }
    if (state === "checking") return strings.t("status.checking")
    if (state === "setup") return strings.t("status.setup")
    if (state === "updates") {
      if (updateCount > 0 && installCount > 0)
        return strings.tn("status.updates", updateCount).arg(Commons.I18n.formatNumber(updateCount))
          + " · " + strings.tn("status.installs", installCount).arg(Commons.I18n.formatNumber(installCount))
      if (installCount > 0)
        return strings.tn("status.installs", installCount).arg(Commons.I18n.formatNumber(installCount))
      return strings.tn("status.updates", updateCount).arg(Commons.I18n.formatNumber(updateCount))
    }
    if (state === "ready") return strings.t("status.ready")
    return strings.t("status.idle")
  }

  readonly property string message: statusText()

  // Asset errors carry the matching filenames in the helper's detail; the
  // panel shows them, the bar tooltip stays one line.
  readonly property string statusLongText: {
    if (state === "error" && statusDetail !== ""
        && (statusCode === "asset_ambiguous" || statusCode === "asset_regex_no_match"))
      return message + "\n" + statusDetail
    return message
  }

  readonly property string noticeText: {
    if (noticeCode !== "") {
      var known = errorText(noticeCode)
      return known !== "" ? known : (noticeDetail !== "" ? noticeDetail : strings.t("errors.unknown"))
    }
    if (noticeKey === "") return ""
    var text = strings.t(noticeKey)
    if (noticeArg1 !== "") text = text.arg(noticeArg1)
    if (noticeArg2 !== "") text = text.arg(noticeArg2)
    if (noticeArg3 !== "") text = text.arg(noticeArg3)
    return text
  }

  // Asset errors carry the matching names or the release's file types in the
  // helper's detail; show it under the localized message.
  readonly property string noticeLongText: {
    if (noticeCode !== "" && noticeDetail !== ""
        && (noticeCode === "asset_ambiguous" || noticeCode === "asset_regex_no_match"
            || noticeCode === "no_supported_asset"))
      return noticeText + "\n" + noticeDetail
    return noticeText
  }

  readonly property string settingsStatusText: {
    if (settingsStatusCode !== "") {
      var known = errorText(settingsStatusCode)
      return known !== "" ? known : strings.t("errors.unknown")
    }
    if (settingsKey === "") return ""
    var text = strings.t(settingsKey)
    return settingsArg !== "" ? text.arg(settingsArg) : text
  }

  function setNotice(key, arg1, arg2, arg3) {
    noticeKey = key
    noticeArg1 = arg1 === undefined ? "" : String(arg1)
    noticeArg2 = arg2 === undefined ? "" : String(arg2)
    noticeArg3 = arg3 === undefined ? "" : String(arg3)
    noticeCode = ""
    noticeDetail = ""
    noticeError = false
  }

  function setNoticeError(code, detail) {
    noticeKey = ""
    noticeArg1 = ""
    noticeArg2 = ""
    noticeArg3 = ""
    noticeCode = code || "unknown"
    noticeDetail = detail || ""
    noticeError = true
  }

  function setSettingsNotice(key, arg) {
    settingsKey = key
    settingsArg = arg === undefined ? "" : String(arg)
    settingsStatusCode = ""
    settingsError = false
  }

  function setSettingsError(code) {
    settingsKey = ""
    settingsArg = ""
    settingsStatusCode = code || "unknown"
    settingsError = true
  }

  function statusLabel(status) {
    var key = "status." + status
    var text = strings.t(key, "")
    return text !== "" ? text : strings.t("status.compare_manually")
  }

  // ---- lifecycle -------------------------------------------------------
  function open() {
    root.controller.show()
    if (root.page !== "list") root.page = "list"
    if (root.state === "idle" || (!root.checking && Date.now() - root.lastCheckAt > 60000))
      root.refresh()
    if (!root.settingsLoaded) root.sendSettings({action: "get"})
  }

  function close() {
    root.pendingRemove = ""
    root.tokenDraft = ""
    root.controller.hide()
  }

  function handleEscape() {
    if (root.pendingRemove !== "") {
      root.pendingRemove = ""
      return
    }
    if (root.page === "settings") {
      root.page = "list"
      return
    }
    root.close()
  }

  function openSettingsPage() {
    root.page = "settings"
    if (!root.settingsLoaded) root.sendSettings({action: "get"})
  }

  onOpenedChanged: {
    if (opened) {
      if (page !== "list") page = "list"
      cursor = -1
      pendingRemove = ""
    }
  }

  // ---- checking --------------------------------------------------------
  function refresh() {
    if (!helperPath) {
      state = "error"
      statusCode = "helper_missing"
      statusDetail = ""
      return
    }
    if (checkProc.running) {
      pendingRefresh = true
      return
    }
    pendingRefresh = false
    lastCheckAt = Date.now()
    checking = true
    checkActive = true
    state = "checking"
    statusCode = ""
    statusDetail = ""
    checkProc.reset()
    checkProc.command = ["python3", helperPath, "check", "--json"]
    checkProc.running = true
    checkTimeout.restart()
  }

  function handleCheckResult(out, err, exitCode) {
    if (!checkActive) return   // the timeout already reported this run
    checkActive = false
    checkTimeout.stop()
    checking = false
    if (String(out).trim() === "") {
      state = "error"
      statusCode = "helper_failed"
      statusDetail = String(err).trim()
    } else {
      applyCheck(out)
    }
    // A refresh requested while this run was in flight is already answered by
    // its result; running a second check right away would only burn API calls.
    pendingRefresh = false
  }

  function applyCheck(raw) {
    var result
    try {
      result = JSON.parse(String(raw || "{}"))
    } catch (e) {
      state = "error"
      statusCode = "bad_response"
      statusDetail = ""
      return
    }
    apps = Array.isArray(result.apps) ? result.apps : []
    checkedCount = Number(result.checked || 0)
    updateCount = Number(result.updates || 0)
    installCount = Number(result.installs || 0)
    var errors = Array.isArray(result.errors) ? result.errors : []
    if (cursor >= apps.length) cursor = -1
    if (errors.length > 0) {
      state = "error"
      statusCode = String(errors[0].code || "unknown")
      statusDetail = String(errors[0].error || "")
    } else if (checkedCount === 0) {
      state = "setup"
      statusCode = ""
      statusDetail = ""
    } else if (updateCount > 0 || installCount > 0) {
      state = "updates"
      statusCode = ""
      statusDetail = ""
    } else {
      state = "ready"
      statusCode = ""
      statusDetail = ""
    }
  }

  function recount() {
    var updates = 0
    var installs = 0
    for (var i = 0; i < apps.length; i++) {
      if (apps[i].status === "update_available") updates += 1
      else if (apps[i].status === "not_installed") installs += 1
    }
    updateCount = updates
    installCount = installs
    checkedCount = apps.length
    state = apps.length === 0 ? "setup" : (updates > 0 || installs > 0 ? "updates" : "ready")
  }

  // ---- cursor + row actions -------------------------------------------
  function moveCursor(delta) {
    if (root.page !== "list" || apps.length === 0) return
    if (cursor < 0) cursor = delta > 0 ? 0 : apps.length - 1
    else cursor = (cursor + delta + apps.length) % apps.length
  }

  function appAt(index) {
    return index >= 0 && index < apps.length ? apps[index] : null
  }

  function activateCursor() {
    var app = appAt(cursor)
    if (!app) return
    if (app.status === "update_available" || app.status === "not_installed") launchUpdate(app.name)
    else openRelease(app.release_url)
  }

  function launchUpdate(name) {
    if (!helperPath) {
      setNoticeError("helper_missing", "")
      return
    }
    var command = "python3 " + Util.shellQuote(helperPath) + " update " + Util.shellQuote(name) + " --apply"
    // The floating terminal needs the keyboard, so the card steps out of the
    // way first: a layer-shell surface that keeps focus would swallow the
    // confirmation and sudo prompts inside the terminal.
    root.close()
    Qt.callLater(function() {
      Quickshell.execDetached(["omarchy-launch-floating-terminal-with-presentation", command])
    })
  }

  function openRelease(url) {
    var target = String(url || "")
    if (!/^https:\/\/(www\.)?github\.com\//.test(target)) return
    Quickshell.execDetached(["xdg-open", target])
  }

  function askRemove(name) {
    if (root.page !== "list") return
    pendingRemove = String(name || "")
  }

  function removeApp(name) {
    if (!helperPath || removeProc.running) return
    noticeArea = "list"
    pendingRemove = ""
    removing = true
    setNotice("notice.removing", name)
    removeProc.reset()
    removeProc.command = ["python3", helperPath, "remove", name, "--json"]
    removeProc.running = true
  }

  function handleRemove(out, err, exitCode) {
    removing = false
    var result = parseJson(out)
    if (!result || result.ok !== true) {
      setNoticeError(result ? result.code : "bad_response", result ? result.error : err)
      return
    }
    apps = apps.filter(function(app) { return app.name !== result.name })
    recount()
    setNotice("notice.removed", result.name)
  }

  function setAppChannel(name, channel) {
    if (!helperPath || channelProc.running) return
    noticeArea = "list"
    switchingChannel = true
    setNotice("")
    channelProc.reset()
    channelProc.command = ["python3", helperPath, "set-channel", name, "--release-channel", channel, "--json"]
    channelProc.running = true
  }

  function handleChannel(out, err, exitCode) {
    switchingChannel = false
    var result = parseJson(out)
    if (!result || result.ok !== true) {
      setNoticeError(result ? result.code : "bad_response", result ? result.error : err)
      return
    }
    setNotice("notice.channel", result.name)
    root.refresh()
  }

  function parseJson(raw) {
    try {
      var value = JSON.parse(String(raw || "{}"))
      return (value && typeof value === "object") ? value : null
    } catch (e) {
      return null
    }
  }

  // ---- add form --------------------------------------------------------
  function formReady() {
    if (formUrl.trim() === "") {
      setNoticeError("form_need_url")
      return false
    }
    if (formMethod === "appimage" && formTarget.trim() === "") {
      setNoticeError("form_need_target")
      return false
    }
    return true
  }

  function submitAdd() {
    noticeArea = "add"
    if (adding || detecting || addProc.running) return
    if (!helperPath) {
      setNoticeError("helper_missing")
      return
    }
    if (!formReady()) return
    var blankVersion = root.addWithoutVersion
    root.addWithoutVersion = false
    if (formVersion.trim() === "" && root.formMethod !== "auto" && !blankVersion) {
      detectVersion(true)
      return
    }
    root.addingWithoutVersion = blankVersion || formVersion.trim() === ""
    adding = true
    setNotice("notice.adding")
    addProc.reset()
    addProc.command = [
      "python3", helperPath, "add",
      "--url", formUrl.trim(),
      "--version", formVersion.trim(),
      "--asset-regex", formRegex.trim(),
      "--install-method", formMethod,
      "--release-channel", formChannel,
      "--name", formName.trim(),
      "--package", formPackage.trim(),
      "--target", formTarget.trim(),
      "--json"
    ]
    addProc.running = true
  }

  function handleAdd(out, err, exitCode) {
    adding = false
    root.addWithoutVersion = false
    var result = parseJson(out)
    if (!result || result.ok !== true) {
      setNoticeError(result ? result.code : "bad_response", result ? result.error : err)
      return
    }
    formUrl = ""
    formName = ""
    formPackage = ""
    formVersion = ""
    formRegex = ""
    formTarget = ""
    formChannel = "stable"
    formMethod = "auto"
    if (result.install_method) {
      var label = strings.t("add.method." + String(result.install_method))
      var path = String(result.target || "")
      if (path !== "") {
        var home = Quickshell.env("HOME")
        if (home && path.indexOf(home + "/") === 0) path = "~" + path.slice(home.length)
        setNotice(result.installed_version === "" ? "notice.addedAutoPathPending" : "notice.addedAutoPath",
          result.name, label, path)
      } else {
        setNotice(result.installed_version === "" ? "notice.addedAutoPending" : "notice.addedAuto",
          result.name, label)
      }
    } else {
      setNotice(root.addingWithoutVersion ? "notice.addedWithoutVersion" : "notice.added", result.name)
    }
    root.addingWithoutVersion = false
    root.refresh()
  }

  function detectVersion(forAdd) {
    noticeArea = "add"
    if (detecting || detectProc.running) {
      if (forAdd) addAfterDetect = true
      return
    }
    if (!helperPath) {
      setNoticeError("helper_missing")
      return
    }
    if (formMethod === "appimage" && formTarget.trim() === "") {
      setNoticeError("form_need_target")
      return
    }
    if (formMethod === "pacman" && formPackage.trim() === "" && formName.trim() === "" && formUrl.trim() === "") {
      setNoticeError("form_need_name")
      return
    }
    detecting = true
    addAfterDetect = forAdd === true
    setNotice("notice.detecting")
    detectProc.reset()
    detectProc.command = [
      "python3", helperPath, "detect-version", formPackage.trim() || formName.trim(),
      "--install-method", formMethod,
      "--target", formTarget.trim(),
      "--url", formUrl.trim(),
      "--json"
    ]
    detectProc.running = true
  }

  function handleDetect(out, err, exitCode) {
    detecting = false
    var shouldAdd = addAfterDetect
    addAfterDetect = false
    var result = parseJson(out)
    if (!result || result.ok !== true) {
      if (shouldAdd) {
        // No installed version to detect: add it anyway and record the
        // version after the first install.
        root.addWithoutVersion = true
        Qt.callLater(root.submitAdd)
        return
      }
      setNoticeError(result ? result.code : "bad_response", result ? result.error : err)
      return
    }
    formVersion = String(result.version || "")
    setNotice("notice.detected", result.version, result.source)
    if (shouldAdd) Qt.callLater(root.submitAdd)
  }

  // ---- settings --------------------------------------------------------
  function sendSettings(payload) {
    if (!helperPath) {
      setSettingsError("helper_missing")
      return
    }
    var line = JSON.stringify(payload) + "\n"
    if (!settingsProc.running) {
      pendingSettings = line
      settingsProc.running = true
    } else {
      settingsProc.write(line)
    }
  }

  function handleSettingsLine(line) {
    var result = parseJson(line)
    if (!result) {
      setSettingsError("bad_response")
      return
    }
    var action = String(result.action || "")
    if (result.token_saved !== undefined) tokenSaved = result.token_saved === true
    if (result.environment_token_available !== undefined) environmentTokenAvailable = result.environment_token_available === true
    if (result.gh_available !== undefined) ghAvailable = result.gh_available === true
    if (result.gh_authenticated !== undefined) ghAuthenticated = result.gh_authenticated === true
    if (result.proxy_saved !== undefined) proxySaved = result.proxy_saved === true
    if (result.proxy_enabled !== undefined) proxyEnabled = result.proxy_enabled === true
    if ((action === "get" || action === "clear-proxy" || action === "save-proxy") && result.proxy_address !== undefined)
      proxyDraft = String(result.proxy_address || "")
    if (!result.ok) {
      if (action === "test-proxy") proxyTesting = false
      setSettingsError(result.code || "unknown")
      return
    }
    if (action === "get") {
      settingsLoaded = true
      setSettingsNotice(settingsKey === "settings.refreshing" ? "settings.refreshed" : "")
      return
    }
    if (action === "test-proxy") {
      proxyTesting = false
      setSettingsNotice("settings.proxyOk", String(result.http_status))
    } else if (action === "save-token") {
      tokenSaved = true
      tokenDraft = ""
      setSettingsNotice("settings.tokenSaved")
    } else if (action === "clear-token") {
      tokenSaved = false
      setSettingsNotice("settings.tokenCleared")
    } else if (action === "save-proxy") {
      proxySaved = Boolean(result.proxy_saved)
      proxyEnabled = result.proxy_enabled === true
      setSettingsNotice("settings.proxySaved")
    } else if (action === "set-proxy-enabled") {
      proxyEnabled = result.proxy_enabled === true
      setSettingsNotice(proxyEnabled ? "settings.proxyEnabled" : "settings.proxyDisabled")
    } else if (action === "clear-proxy") {
      proxySaved = false
      proxyEnabled = false
      proxyDraft = ""
      setSettingsNotice("settings.proxyCleared")
    } else if (action === "save-interval") {
      setSettingsNotice("settings.intervalSaved")
    } else {
      setSettingsNotice("")
    }
  }

  function saveToken() {
    if (tokenDraft === "") return
    sendSettings({action: "save-token", token: tokenDraft})
    setSettingsNotice("settings.tokenSaving")
  }

  function clearToken() {
    sendSettings({action: "clear-token"})
    setSettingsNotice("settings.tokenClearing")
  }

  function startGhLogin() {
    if (!ghAvailable) {
      setSettingsError("gh_missing")
      return
    }
    root.close()
    Qt.callLater(function() {
      Quickshell.execDetached(["omarchy-launch-floating-terminal-with-presentation", "gh auth login"])
    })
  }

  function refreshLoginStatus() {
    sendSettings({action: "get"})
    setSettingsNotice("settings.refreshing")
  }

  function saveProxy() {
    sendSettings({action: "save-proxy", proxy_address: proxyDraft, proxy_enabled: proxyEnabled})
    setSettingsNotice("settings.proxySaving")
  }

  function setProxyEnabled(enabled) {
    if (enabled && !proxySaved && proxyDraft.trim() === "") {
      setSettingsError("proxy_invalid")
      return
    }
    if (enabled && !proxySaved) {
      sendSettings({action: "save-proxy", proxy_address: proxyDraft, proxy_enabled: true})
      setSettingsNotice("settings.proxySaving")
      return
    }
    sendSettings({action: "set-proxy-enabled", enabled: enabled})
    setSettingsNotice(enabled ? "settings.proxyEnabling" : "settings.proxyDisabling")
  }

  function testProxy() {
    if (proxyTesting) return
    proxyTesting = true
    setSettingsNotice("settings.proxyTesting")
    sendSettings({action: "test-proxy", proxy_address: proxyDraft.trim()})
  }

  function clearProxy() {
    sendSettings({action: "clear-proxy"})
    setSettingsNotice("settings.proxyClearing")
  }

  function saveInterval(hours) {
    sendSettings({action: "save-interval", hours: Number(hours)})
    setSettingsNotice("settings.intervalSaving")
  }

  // ---- processes -------------------------------------------------------
  component JsonTask: Process {
    id: task
    property string out: ""
    property string err: ""
    property int exitCode: -1
    property bool outDone: false
    property bool errDone: false
    property bool done: false
    signal completed(string out, string err, int exitCode)

    function reset() {
      task.out = ""
      task.err = ""
      task.exitCode = -1
      task.outDone = false
      task.errDone = false
      task.done = false
    }

    function maybeFinish() {
      if (task.done || !task.outDone || !task.errDone || task.exitCode < 0) return
      task.done = true
      task.completed(task.out, task.err, task.exitCode)
    }

    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        task.out = String(text || "")
        task.outDone = true
        task.maybeFinish()
      }
    }
    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        task.err = String(text || "")
        task.errDone = true
        task.maybeFinish()
      }
    }
    onExited: function(exitCode) {
      task.exitCode = exitCode
      task.maybeFinish()
    }
  }

  JsonTask {
    id: checkProc
    command: []
    onCompleted: function(out, err, exitCode) { root.handleCheckResult(out, err, exitCode) }
  }

  JsonTask {
    id: addProc
    command: []
    onCompleted: function(out, err, exitCode) { root.handleAdd(out, err, exitCode) }
  }

  JsonTask {
    id: removeProc
    command: []
    onCompleted: function(out, err, exitCode) { root.handleRemove(out, err, exitCode) }
  }

  JsonTask {
    id: channelProc
    command: []
    onCompleted: function(out, err, exitCode) { root.handleChannel(out, err, exitCode) }
  }

  JsonTask {
    id: detectProc
    command: []
    onCompleted: function(out, err, exitCode) { root.handleDetect(out, err, exitCode) }
  }

  Timer {
    id: checkTimeout
    interval: 120000
    repeat: false
    onTriggered: {
      if (!root.checkActive) return
      root.checkActive = false
      root.checking = false
      root.pendingRefresh = false
      if (checkProc.running) checkProc.running = false
      root.state = "error"
      root.statusCode = "check_timeout"
      root.statusDetail = ""
    }
  }

  Timer {
    id: autoTimer
    interval: Math.max(1, root.configuredIntervalHours) * 3600000
    running: root.autoCheckEnabled
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  Process {
    id: settingsProc
    command: ["python3", root.helperPath, "settings-service"]
    stdinEnabled: true
    stdout: SplitParser {
      onRead: function(line) { root.handleSettingsLine(line) }
    }
    onStarted: {
      if (root.pendingSettings !== "") {
        settingsProc.write(root.pendingSettings)
        root.pendingSettings = ""
      }
    }
  }

  ShellIpc {
    target: root.ipcTarget

    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): void { root.refresh() }
  }

  // ---- card ------------------------------------------------------------
  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    centerOnBar: false
    focusTarget: panel.loadedContent
    contentWidth: panel.fittedContentWidth(Style.space(620))
    contentHeight: panel.fittedContentHeight(panel.loadedContentHeight, Style.space(820))

    content: FocusScope {
      id: page
      implicitHeight: column.implicitHeight
      focus: true

      // Text fields own their keys; everything else is panel navigation.
      readonly property bool editorFocused: urlField.activeFocus || nameField.activeFocus
        || packageField.activeFocus || versionField.activeFocus || regexField.activeFocus
        || targetField.activeFocus || tokenField.activeFocus || proxyField.activeFocus

      Keys.onEscapePressed: function(event) {
        root.handleEscape()
        event.accepted = true
      }
      Keys.onUpPressed: function(event) {
        if (page.editorFocused) return
        root.moveCursor(-1)
        event.accepted = true
      }
      Keys.onDownPressed: function(event) {
        if (page.editorFocused) return
        root.moveCursor(1)
        event.accepted = true
      }
      Keys.onReturnPressed: function(event) {
        if (page.editorFocused) return
        root.activateCursor()
        event.accepted = true
      }
      Keys.onPressed: function(event) {
        if (page.editorFocused || event.modifiers !== Qt.NoModifier) return
        if (event.text === "r") {
          root.refresh()
          event.accepted = true
        } else if (event.text === "x") {
          var app = root.appAt(root.cursor)
          if (app) { root.askRemove(app.name); event.accepted = true }
        } else if (event.text === "s") {
          var chosen = root.appAt(root.cursor)
          if (chosen) {
            root.setAppChannel(chosen.name, chosen.release_channel === "development" ? "stable" : "development")
            event.accepted = true
          }
        }
      }

      Flickable {
        id: flick
        anchors.fill: parent
        contentWidth: width
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height

        Column {
          id: column
          width: flick.width
          spacing: Style.spacing.panelGap

          // ---- header ------------------------------------------------
          Row {
            width: parent.width
            spacing: Style.spacing.controlGap

            Text {
              textFormat: Text.PlainText
              width: parent.width - settingsButton.width - closeButton.width - parent.spacing * 2
              text: root.page === "settings" ? strings.t("settings.title") : strings.t("panel.title")
              color: Commons.Color.popups.text
              font.family: Style.font.family
              font.pixelSize: Style.font.title
              font.bold: true
              elide: Text.ElideRight
            }

            Button {
              id: settingsButton
              visible: root.page !== "settings"
              text: strings.t("panel.settings")
              focusable: true
              fontSize: Style.font.caption
              verticalPadding: Style.spacing.controlPaddingY
              onClicked: root.openSettingsPage()
            }

            Button {
              id: closeButton
              text: root.page === "settings" ? strings.t("settings.back") : strings.t("panel.close")
              focusable: true
              fontSize: Style.font.caption
              verticalPadding: Style.spacing.controlPaddingY
              onClicked: {
                if (root.page === "settings") root.page = "list"
                else root.close()
              }
            }
          }

          // ---- list page ---------------------------------------------
          Column {
            width: parent.width
            spacing: Style.spacing.panelGap
            visible: root.page !== "settings"

            Text {
              textFormat: Text.PlainText
              width: parent.width
              text: strings.t("panel.subtitle")
              color: Commons.Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            Row {
              width: parent.width
              spacing: Style.spacing.controlGap

              Text {
                textFormat: Text.PlainText
                width: parent.width - checkButton.width - parent.spacing
                text: root.statusLongText
                color: root.state === "error" ? Commons.Color.urgent : Commons.Color.muted
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
                wrapMode: Text.WordWrap
                anchors.verticalCenter: parent.verticalCenter
              }

              Button {
                id: checkButton
                text: root.checking ? strings.t("panel.checking") : strings.t("panel.checkNow")
                enabled: !root.checking
                focusable: true
                fontSize: Style.font.caption
                verticalPadding: Style.spacing.controlPaddingY
                onClicked: root.refresh()
              }
            }

            Text {
              textFormat: Text.PlainText
              width: parent.width
              visible: root.noticeText !== "" && root.noticeArea === "list"
              text: root.noticeLongText
              color: root.noticeError ? Commons.Color.urgent : Commons.Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            // Inline removal confirmation: no second surface, so the panel
            // keeps the keyboard and the bar can still be clicked.
            BorderSurface {
              visible: root.pendingRemove !== ""
              width: parent.width
              padding: Style.spacing.xxl
              radius: Style.cornerRadius
              color: "transparent"
              borderSpec: Border.controlSpec("normal", Commons.Color.popups.text, Commons.Color.accent)

              Column {
                width: parent.width - parent.leftPadding - parent.rightPadding
                spacing: Style.spacing.sm

                Text {
                  textFormat: Text.PlainText
                  width: parent.width
                  text: strings.t("remove.title")
                  color: Commons.Color.popups.text
                  font.family: Style.font.family
                  font.pixelSize: Style.font.body
                  font.bold: true
                }
                Text {
                  textFormat: Text.PlainText
                  width: parent.width
                  text: strings.t("remove.body").arg(root.pendingRemove)
                  color: Commons.Color.muted
                  font.family: Style.font.family
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WordWrap
                }
                Row {
                  width: parent.width
                  spacing: Style.spacing.controlGap
                  Item { width: parent.width - cancelRemove.width - confirmRemove.width - parent.spacing * 2; height: 1 }
                  Button {
                    id: cancelRemove
                    text: strings.t("remove.cancel")
                    focusable: true
                    fontSize: Style.font.caption
                    verticalPadding: Style.spacing.controlPaddingY
                    onClicked: root.pendingRemove = ""
                  }
                  Button {
                    id: confirmRemove
                    text: strings.t("remove.confirm")
                    enabled: !root.removing
                    focusable: true
                    fontSize: Style.font.caption
                    verticalPadding: Style.spacing.controlPaddingY
                    onClicked: root.removeApp(root.pendingRemove)
                  }
                }
              }
            }

            PanelSectionHeader {
              text: strings.t("panel.registered")
              foreground: Commons.Color.popups.text
            }

            Text {
              textFormat: Text.PlainText
              width: parent.width
              visible: root.apps.length === 0
              text: strings.t("panel.empty")
              color: Commons.Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            Column {
              id: rowList
              width: parent.width
              spacing: Style.spacing.sm

              Repeater {
                model: root.apps
                delegate: AppRow {
                  width: rowList.width
                }
              }
            }

            PanelSeparator { width: parent.width; foreground: Commons.Color.popups.text }

            PanelSectionHeader {
              text: strings.t("add.title")
              foreground: Commons.Color.popups.text
            }

            TextField {
              id: urlField
              width: parent.width
              text: root.formUrl
              placeholderText: strings.t("add.urlPlaceholder")
              selectByMouse: true
              onTextChanged: if (root.formUrl !== text) root.formUrl = text
            }

            Row {
              width: parent.width
              spacing: Style.spacing.controlGap

              TextField {
                id: nameField
                width: (parent.width - parent.spacing * 2) / 3
                text: root.formName
                placeholderText: strings.t("add.namePlaceholder")
                selectByMouse: true
                onTextChanged: if (root.formName !== text) root.formName = text
              }
              TextField {
                id: packageField
                width: (parent.width - parent.spacing * 2) / 3
                text: root.formPackage
                placeholderText: strings.t("add.packagePlaceholder")
                selectByMouse: true
                onTextChanged: if (root.formPackage !== text) root.formPackage = text
              }
              TextField {
                id: versionField
                width: (parent.width - parent.spacing * 2) / 3
                text: root.formVersion
                placeholderText: strings.t("add.versionPlaceholder")
                selectByMouse: true
                onTextChanged: if (root.formVersion !== text) root.formVersion = text
              }
            }

            Row {
              width: parent.width
              spacing: Style.spacing.controlGap

              Dropdown {
                width: (parent.width - parent.spacing) / 2
                label: strings.t("add.method")
                value: root.formMethod
                options: [
                  { value: "auto", label: strings.t("add.method.auto") },
                  { value: "pacman", label: strings.t("add.method.pacman") },
                  { value: "appimage", label: strings.t("add.method.appimage") }
                ]
                onChanged: function(value) { root.formMethod = value }
              }

              Dropdown {
                width: (parent.width - parent.spacing) / 2
                label: strings.t("add.channel")
                value: root.formChannel
                options: [
                  { value: "stable", label: strings.t("add.channel.stable") },
                  { value: "development", label: strings.t("add.channel.development") }
                ]
                onChanged: function(value) { root.formChannel = value }
              }
            }

            TextField {
              id: regexField
              width: parent.width
              text: root.formRegex
              placeholderText: strings.t("add.regexPlaceholder")
              selectByMouse: true
              onTextChanged: if (root.formRegex !== text) root.formRegex = text
            }

            TextField {
              id: targetField
              width: parent.width
              visible: root.formMethod === "appimage"
              text: root.formTarget
              placeholderText: strings.t("add.targetPlaceholder")
              selectByMouse: true
              onTextChanged: if (root.formTarget !== text) root.formTarget = text
            }

            Text {
              textFormat: Text.PlainText
              width: parent.width
              text: strings.t("add.hint")
              color: Commons.Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            Row {
              width: parent.width
              spacing: Style.spacing.controlGap

              Item {
                width: parent.width - addButton.width - parent.spacing
                  - (detectButton.visible ? detectButton.width + parent.spacing : 0)
                height: 1
              }

              Button {
                id: detectButton
                visible: root.formMethod !== "auto"
                text: root.detecting ? strings.t("add.detecting") : strings.t("add.detect")
                enabled: !root.adding && !root.detecting
                focusable: true
                fontSize: Style.font.caption
                verticalPadding: Style.spacing.controlPaddingY
                onClicked: root.detectVersion(false)
              }

              Button {
                id: addButton
                text: root.adding ? strings.t("add.adding") : strings.t("add.submit")
                enabled: !root.adding && !root.detecting
                focusable: true
                fontSize: Style.font.caption
                verticalPadding: Style.spacing.controlPaddingY
                onClicked: root.submitAdd()
              }
            }

            Text {
              textFormat: Text.PlainText
              width: parent.width
              visible: root.noticeText !== "" && root.noticeArea === "add"
              text: root.noticeLongText
              color: root.noticeError ? Commons.Color.urgent : Commons.Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }
          }

          // ---- settings page -----------------------------------------
          Column {
            width: parent.width
            spacing: Style.spacing.panelGap
            visible: root.page === "settings"

            PanelSectionHeader {
              text: strings.t("settings.token.title")
              foreground: Commons.Color.popups.text
            }

            Text {
              textFormat: Text.PlainText
              width: parent.width
              text: root.environmentTokenAvailable ? strings.t("settings.token.env")
                : root.tokenSaved ? strings.t("settings.token.saved")
                : root.ghAuthenticated ? strings.t("settings.token.gh")
                : strings.t("settings.token.none")
              color: Commons.Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            TextField {
              id: tokenField
              width: parent.width
              text: root.tokenDraft
              password: true
              placeholderText: strings.t("settings.token.placeholder")
              selectByMouse: true
              onTextChanged: if (root.tokenDraft !== text) root.tokenDraft = text
            }

            Row {
              width: parent.width
              spacing: Style.spacing.controlGap

              Button {
                text: strings.t("settings.token.save")
                enabled: root.tokenDraft !== ""
                focusable: true
                fontSize: Style.font.caption
                verticalPadding: Style.spacing.controlPaddingY
                onClicked: root.saveToken()
              }
              Button {
                text: strings.t("settings.token.clear")
                enabled: root.tokenSaved
                focusable: true
                fontSize: Style.font.caption
                verticalPadding: Style.spacing.controlPaddingY
                onClicked: root.clearToken()
              }
              Button {
                text: strings.t("settings.token.ghLogin")
                enabled: root.ghAvailable
                focusable: true
                fontSize: Style.font.caption
                verticalPadding: Style.spacing.controlPaddingY
                onClicked: root.startGhLogin()
              }
              Button {
                text: strings.t("settings.token.refresh")
                focusable: true
                fontSize: Style.font.caption
                verticalPadding: Style.spacing.controlPaddingY
                onClicked: root.refreshLoginStatus()
              }
            }

            PanelSeparator { width: parent.width; foreground: Commons.Color.popups.text }

            PanelSectionHeader {
              text: strings.t("settings.proxy.title")
              foreground: Commons.Color.popups.text
            }

            Text {
              textFormat: Text.PlainText
              width: parent.width
              text: root.proxySaved ? strings.t("settings.proxy.saved") : strings.t("settings.proxy.none")
              color: Commons.Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            TextField {
              id: proxyField
              width: parent.width
              text: root.proxyDraft
              placeholderText: strings.t("settings.proxy.placeholder")
              selectByMouse: true
              onTextChanged: if (root.proxyDraft !== text) root.proxyDraft = text
            }

            Toggle {
              width: parent.width
              label: strings.t("settings.proxy.enable")
              checked: root.proxyEnabled
              onClicked: root.setProxyEnabled(!root.proxyEnabled)
            }

            Row {
              width: parent.width
              spacing: Style.spacing.controlGap

              Button {
                text: strings.t("settings.proxy.save")
                focusable: true
                fontSize: Style.font.caption
                verticalPadding: Style.spacing.controlPaddingY
                onClicked: root.saveProxy()
              }
              Button {
                text: root.proxyTesting ? strings.t("settings.proxy.testing") : strings.t("settings.proxy.test")
                enabled: !root.proxyTesting && root.proxyDraft.trim() !== ""
                focusable: true
                fontSize: Style.font.caption
                verticalPadding: Style.spacing.controlPaddingY
                onClicked: root.testProxy()
              }
              Button {
                text: strings.t("settings.proxy.clear")
                enabled: root.proxySaved
                focusable: true
                fontSize: Style.font.caption
                verticalPadding: Style.spacing.controlPaddingY
                onClicked: root.clearProxy()
              }
            }

            PanelSeparator { width: parent.width; foreground: Commons.Color.popups.text }

            PanelSectionHeader {
              text: strings.t("settings.interval.title")
              foreground: Commons.Color.popups.text
            }

            Dropdown {
              width: parent.width
              value: String(root.configuredIntervalHours)
              options: [
                { value: "0", label: strings.t("settings.interval.off") },
                { value: "1", label: strings.t("settings.interval.1") },
                { value: "3", label: strings.t("settings.interval.3") },
                { value: "6", label: strings.t("settings.interval.6") },
                { value: "12", label: strings.t("settings.interval.12") },
                { value: "24", label: strings.t("settings.interval.24") },
                { value: "48", label: strings.t("settings.interval.48") },
                { value: "168", label: strings.t("settings.interval.168") }
              ]
              onChanged: function(value) { root.saveInterval(value) }
            }

            Text {
              textFormat: Text.PlainText
              width: parent.width
              text: root.settingsStatusText
              color: root.settingsError ? Commons.Color.urgent : Commons.Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            PanelSeparator { width: parent.width; foreground: Commons.Color.popups.text }

            Text {
              textFormat: Text.PlainText
              width: parent.width
              text: strings.t("settings.about").arg(root.pluginVersion)
              color: Commons.Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }

            Text {
              textFormat: Text.PlainText
              width: parent.width
              text: strings.t("settings.language")
              color: Commons.Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }
          }
        }
      }
    }
  }

  // ---- one registered app ---------------------------------------------
  component AppRow: CursorSurface {
    id: row
    // The Repeater injects these two into every delegate.
    required property var modelData
    required property int index
    readonly property var app: modelData
    readonly property int appIndex: index

    readonly property bool failed: !app || app.status === "check_failed"
    readonly property bool hasUpdate: !!app
      && (app.status === "update_available" || app.status === "not_installed")
    readonly property bool notInstalled: !!app && app.status === "not_installed"
    readonly property string rowStatus: app ? String(app.status || "") : ""
    readonly property string versionLine: app
      ? (String(app.current || "") !== "" ? String(app.current) : strings.t("app.notInstalled"))
        + " → " + String(app.latest || "?")
        + (app.release_channel === "development" ? " · " + strings.t("app.dev") : "")
      : ""
    readonly property string detailLine: app
      ? String(app.repo || "") + " · " + String(app.asset || "")
      : ""

    width: parent ? parent.width : 0
    implicitHeight: rowBody.implicitHeight + Style.spacing.xl * 2
    hasCursor: root.cursor === row.appIndex
    foreground: Commons.Color.popups.text
    padding: Style.spacing.xl

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      acceptedButtons: Qt.LeftButton
      onContainsMouseChanged: if (containsMouse) root.cursor = row.appIndex
      onClicked: {
        root.cursor = row.appIndex
        if (row.hasUpdate) root.activateCursor()
      }
    }

    Column {
      id: rowBody
      x: row.contentLeftInset
      y: row.contentTopInset
      width: row.width - row.contentLeftInset - row.contentRightInset
      spacing: Style.spacing.xs

      Row {
        width: parent.width
        spacing: Style.spacing.controlGap

        Text {
          textFormat: Text.PlainText
          width: parent.width - rowStatusText.width - parent.spacing
          text: row.app ? row.app.name + "  " + row.versionLine : ""
          color: Commons.Color.popups.text
          font.family: Style.font.family
          font.pixelSize: Style.font.body
          elide: Text.ElideRight
        }

        Text {
          id: rowStatusText
          textFormat: Text.PlainText
          text: root.statusLabel(row.rowStatus)
          color: row.failed ? Commons.Color.urgent : row.hasUpdate ? Commons.Color.accent : Commons.Color.muted
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          anchors.verticalCenter: parent.verticalCenter
        }
      }

      Text {
        textFormat: Text.PlainText
        width: parent.width
        text: row.failed && row.app && row.app.error_code !== ""
          ? row.detailLine + " · " + (root.errorText(row.app.error_code) || root.statusLabel("check_failed"))
          : row.detailLine
        color: Commons.Color.muted
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        elide: Text.ElideRight
      }

      Row {
        spacing: Style.spacing.sm
        visible: !!row.app

        Button {
          visible: row.hasUpdate
          text: strings.t(row.notInstalled ? "app.install" : "app.update")
          fontSize: Style.font.caption
          verticalPadding: Style.spacing.xs
          onClicked: root.launchUpdate(row.app.name)
        }
        Button {
          text: row.app && row.app.release_channel === "development"
            ? strings.t("app.switchToStable") : strings.t("app.switchToDev")
          enabled: !root.switchingChannel
          fontSize: Style.font.caption
          verticalPadding: Style.spacing.xs
          onClicked: root.setAppChannel(row.app.name,
            row.app.release_channel === "development" ? "stable" : "development")
        }
        Button {
          text: strings.t("app.release")
          fontSize: Style.font.caption
          verticalPadding: Style.spacing.xs
          onClicked: root.openRelease(row.app.release_url)
        }
        Button {
          text: strings.t("app.remove")
          enabled: !root.removing && !root.switchingChannel
          fontSize: Style.font.caption
          verticalPadding: Style.spacing.xs
          onClicked: root.askRemove(row.app.name)
        }
      }
    }
  }
}
