/* NeedleDropper Connect page.
 *
 * Reads the per-process API token from <meta name="nd-token"> and talks to the companion's
 * JSON API (/api/*, header X-ND-Token). The state is polled every few seconds; the companion
 * also nudges this page over /events?client=panel when something changes (a test signal from
 * World of Warcraft, Spotify connecting, ...), so the Check-the-link tick appears at once.
 * No inline script or style anywhere (the page's CSP forbids both).
 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const TOKEN = (document.querySelector('meta[name="nd-token"]') || {}).content || "";
  const POLL_MS = 2000, FAST_POLL_MS = 1000, HIDDEN_POLL_MS = 10000;
  const GLYPHS = { ok: "✓", wait: "…", warn: "!", bad: "×", off: "–" };
  const TRANSPORT_NAMES = { pixel: "pixel link", chatlog: "chat-log link" };
  // The music choices are set in game (Options > AddOns > NeedleDropper > Music services): shown here read-only.
  const ORDER_TEXT = { "spotify-youtube": "Spotify, then YouTube", "youtube-spotify": "YouTube, then Spotify",
                       spotify: "Spotify only", youtube: "YouTube only", none: "Neither: title cards and in-game files only" };
  const CORNER_TEXT = { topright: "Top right", topleft: "Top left (below the beacon)", bottomright: "Bottom right",
                        bottomleft: "Bottom left" };
  const APP_DEVICE = "NeedleDropper"; // in-app Spotify's own Connect device: chosen as "this app", never listed
  const PENDING_MS = 25000;
  const pending = {}; // action -> { id, since } of a request this page sent and still waits for

  let state = null;
  let pollTimer = null;
  let refreshing = false;
  let refreshAgain = false;
  let offline = false;
  let tokenRejected = false;
  let autostartTouched = false;
  let devicesLoaded = false;
  let lastHeardCount = null;
  let lastSpotifyState = null;
  let wowFormOpenedByUser = false;

  // ------------------------------------------------------------------ api --
  async function api(path, { method = "GET", body } = {}) {
    const init = {
      method,
      headers: { "X-ND-Token": TOKEN, Accept: "application/json" },
      cache: "no-store",
      credentials: "same-origin",
    };
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    let resp;
    try {
      resp = await fetch(path, init);
    } catch (e) {
      const err = new Error("NeedleDropper is not reachable. Is it still running?");
      err.network = true;
      throw err;
    }
    const text = await resp.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch (e) { data = { error: text || resp.statusText }; }
    if (!resp.ok) {
      const err = new Error((data && data.error) || `Request failed (HTTP ${resp.status}).`);
      err.status = resp.status;
      err.data = data;
      if (resp.status === 401) tokenRejected = true;
      throw err;
    }
    return data;
  }

  // ------------------------------------------------------------ feedback --
  let toastTimer = null;
  function announce(msg) {
    if (!msg) return;
    const live = $("announcer");
    live.textContent = "";
    setTimeout(() => { live.textContent = msg; }, 50);
  }
  function toast(msg, kind) {
    if (!msg) return;
    const t = $("toast");
    t.textContent = msg;
    t.className = "toast" + (kind ? " " + kind : "");
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, kind === "error" ? 9000 : 5000);
    announce(msg);
  }
  $("toast").addEventListener("click", () => { $("toast").hidden = true; });

  async function busy(btn, label, fn) {
    if (!btn || btn.getAttribute("aria-busy") === "true") return undefined;
    const old = btn.textContent;
    btn.setAttribute("aria-busy", "true");
    btn.disabled = true;
    if (label) btn.textContent = label;
    try {
      return await fn();
    } catch (e) {
      toast(e.message, "error");
      return undefined;
    } finally {
      btn.removeAttribute("aria-busy");
      btn.disabled = false;
      if (label) btn.textContent = old;
      if (state) render(state);
    }
  }

  function fieldError(input, errorEl, msg) {
    if (msg) {
      input.setAttribute("aria-invalid", "true");
      errorEl.textContent = msg;
    } else {
      input.removeAttribute("aria-invalid");
      errorEl.textContent = "";
    }
  }

  async function saveConfig(patch, okMsg) {
    const r = await api("/api/config", { method: "POST", body: patch });
    if (r.state) apply(r.state);
    (r.warnings || []).forEach((w) => toast(w, "error"));
    if (okMsg && !(r.warnings || []).length) toast(okMsg, "ok");
    return r;
  }

  // ---------------------------------------------------------- utilities --
  function setText(el, text) { if (el.textContent !== text) el.textContent = text; }
  function setVal(el, value) {
    const v = value == null ? "" : String(value);
    if (document.activeElement === el || el.dataset.dirty === "1") return;
    if (el.value !== v) el.value = v;
  }
  function markDirty(el) { el.addEventListener("input", () => { el.dataset.dirty = "1"; }); }
  function clean(el) { delete el.dataset.dirty; }
  function show(el, on) { el.hidden = !on; }

  function setStatus(id, kind, text) {
    const el = $(id);
    el.className = "status " + kind;
    setText(el.querySelector(".glyph"), GLYPHS[kind] || "");
    setText(el.querySelector(".status-text"), text);
    const card = el.closest(".card");
    if (card) {
      card.classList.toggle("is-ok", kind === "ok");
      card.classList.toggle("is-warn", kind === "warn");
      card.classList.toggle("is-bad", kind === "bad");
    }
  }

  function ago(sec) {
    if (sec == null) return "";
    if (sec < 3) return "just now";
    if (sec < 90) return `${Math.round(sec)} s ago`;
    if (sec < 5400) return `${Math.round(sec / 60)} min ago`;
    return `${Math.round(sec / 3600)} h ago`;
  }

  async function copyText(text, el) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch (e) { /* fall back to selecting the text */ }
    const range = document.createRange();
    range.selectNodeContents(el);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    return false;
  }

  // ---------------------------------------------------------------- render --
  function apply(st) {
    state = st;
    offline = false;
    render(st);
  }

  function render(st) {
    renderApp(st);
    renderWow(st.wow || {}, st.addon || {});
    renderSpotify(st.spotify || {}, st.music || {});
    renderYouTube(st.youtube || {}, st.music || {});
    renderLink(st.link || {}, st.wow || {});
    renderFooter(st.app || {}, st.autostart || {});
    renderBanner(st);
  }

  // ------------------------------------------------------------ requests --
  // Test Spotify / Test YouTube / Sign in to YouTube run the same handlers as the requests sent
  // from the game; their results arrive in state.music.actions (newest last).
  function clock(at) {
    if (!at) return "";
    const d = new Date(at * 1000);
    return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }

  function lastResult(music, action) {
    // "opened" is final for the YouTube sign-in, interim for Connect Spotify
    const list = (music.actions || []).filter((a) => a.action === action &&
                                                     (a.result !== "opened" || action === "youtube-sign-in"));
    return list.length ? list[list.length - 1] : null;
  }

  // One result line for one or more requests ([[action, text while waiting], ...]): a request this page
  // sent and still waits for, else the newest result of any of them.
  function renderResult(el, music, requests) {
    let waiting = "", newest = null;
    requests.forEach(([action, waitText]) => {
      const p = pending[action];
      const last = lastResult(music, action);
      if (p && last && last.id >= p.id) {
        delete pending[action];
        announce(last.text || last.result);
      } else if (p && Date.now() - p.since > PENDING_MS) {
        delete pending[action];
      }
      if (pending[action]) waiting = waitText;
      if (last && (!newest || last.at >= newest.at)) newest = last;
    });
    setText(el, waiting || (newest ? `${clock(newest.at)}: ${newest.text || newest.result}` : ""));
  }

  async function sendRequest(btn, path, body, action, label) {
    await busy(btn, label, async () => {
      const r = await api(path, { method: "POST", body });
      pending[action] = { id: r.id || Math.floor(Date.now() / 1000), since: Date.now() };
      if (state) render(state);
      schedule(500);
    });
  }

  function renderApp(st) {
    const app = st.app || {};
    const el = $("app-status");
    el.className = "app-status " + (offline ? "bad" : "ok");
    setText($("app-status-text"), offline ? "NeedleDropper is not running" : (app.status || "Running"));
    setText($("version"), app.version || "");
  }

  function renderBanner(st) {
    const banner = $("banner");
    let text = "", logs = false, info = false, reload = false;
    if (tokenRejected) {
      text = "This page belongs to an earlier start of NeedleDropper. Reload it to continue.";
      reload = true;
    } else if (offline) {
      text = "NeedleDropper is not running any more. Start it again (NeedleDropper.exe), then reload this page.";
      reload = true;
    } else if ((st.faults || []).length) {
      const f = st.faults[0];
      text = `${f.component} stopped because of an error${f.restarted ? " and was restarted" : ""}. ` +
             "The details are in the log.";
      logs = true;
    } else if (st.app && st.app.tempLocation) {
      // Started from inside the zip (Explorer unpacks it into a temporary folder): journey review F10.
      text = "NeedleDropper is running from a temporary folder (inside the zip?). Extract the zip to a " +
             "permanent folder, such as Documents\\NeedleDropper, and start it from there: Windows cleans " +
             "temporary folders, and Start with Windows would stop working.";
    } else if (st.app && st.app.paused) {
      text = "Needle drops are paused. Resume them from the NeedleDropper icon in the tray.";
      info = true;
    }
    banner.classList.toggle("info", info);
    show(banner, !!text);
    setText($("banner-text"), text);
    show($("banner-logs"), logs);
    show($("banner-reload"), reload);
  }

  const SOURCE_TEXT = { registry: "found in the Windows registry", default: "found in the default folder",
                        config: "chosen by you" };

  function renderWow(wow, addon) {
    const folder = $("wow-folder");
    setText(folder, wow.found ? (wow.folder || wow.root || "") : "not found");
    folder.classList.toggle("path", !!wow.found);
    setText($("wow-source"), wow.found ? `(${SOURCE_TEXT[wow.source] || "chosen by you"})` : "");
    setVal($("wow-root"), wow.root || "");
    const change = $("wow-change");
    if (!wow.found && !wowFormOpenedByUser) change.open = true;

    let line = "", note = "", status = ["wait", "Checking…"], actions = false, install = false;
    let installLabel = "Install addon";
    if (!wow.found) {
      line = "Choose your World of Warcraft folder first.";
      status = ["bad", "Not found"];
    } else if (addon.installed) {
      line = `NeedleDropper ${addon.version || ""} is installed`.replace("  ", " ");
      status = ["ok", "Ready"];
      if (addon.owner === "companion" && addon.updateAvailable) {
        note = addon.legacy
          ? `This is the NeedleDropper ${addon.version || "1.1"} addon. Update it to version ${addon.bundledVersion || "1.2"}, which comes with this app.`
          : `Version ${addon.bundledVersion} comes with this app.`;
        status = ["warn", "Update available"];
        actions = true;
        install = true;
        installLabel = "Update addon";
      } else if (addon.owner === "manager") {
        note = `Installed through ${addon.managedBy || "an addon manager"}. Update it there.`;
      } else if (addon.owner === "other") {
        note = "Installed by an addon manager or by hand. Update it the same way.";
      }
    } else {
      line = "Not installed yet.";
      status = ["warn", "Addon missing"];
      actions = true;
      install = !!addon.canInstall;
      if ((addon.managers || []).length) {
        note = `Your addons are managed by ${addon.managers[0]}: install NeedleDropper there (search for NeedleDropper).`;
      }
    }
    if (wow.found && addon.lastInstall && addon.lastInstall.ok && !note) {
      note = addon.lastInstall.message || "";
    }
    if (addon.outdatedInGame) {
      note = (note ? note + " " : "") + "The game is running an older NeedleDropper addon (1.1 or earlier): " +
             "update it, then restart World of Warcraft.";
      status = ["warn", "Addon too old"];
    } else if (addon.installed && addon.tooOld) {
      // 1.2.1 changed how the addon and the app talk: an older addon and this app can't read each other.
      const how = addon.owner === "companion" ? "with Update addon below" :
                  addon.owner === "manager" ? `in ${addon.managedBy || "your addon manager"}` :
                  "the same way you installed it";
      note = `This app needs NeedleDropper ${addon.minVersion || "1.2.1"} or newer in the game: the addon and the ` +
             `app must be updated together. Update the addon ${how}, then restart World of Warcraft.`;
      status = ["warn", "Addon too old"];
    }
    if (wow.found && wow.running && install) {
      note = (note ? note + " " : "") + "World of Warcraft is running: restart it after installing.";
    } else if (wow.found && wow.running && addon.dataNeedsRestart) {
      // The app made NeedleDropper_Companion while the game ran: the game finds new addon folders
      // only when it starts (journey review F6).
      note = (note ? note + " " : "") + "Restart World of Warcraft once: it only finds NeedleDropper's data addon " +
             "when it starts (until then the zone music isn't faded for the app's songs).";
      if (status[0] === "ok") status = ["warn", "Restart the game"];
    }
    setText($("addon-line"), line);
    setText($("addon-note"), note);
    show($("addon-note"), !!note);
    show($("addon-actions"), actions);
    const get = $("addon-get");
    if (addon.pageUrl && get.getAttribute("href") !== addon.pageUrl) get.setAttribute("href", addon.pageUrl);
    show(get, !addon.installed);
    // The copy that comes with this app is the first choice when it can be installed: the addon site may not
    // list NeedleDropper yet (a private beta, review ON-5).
    get.className = install ? "btn" : "btn primary";
    const btn = $("addon-install");
    btn.className = install ? "btn primary" : "btn";
    show(btn, install);
    if (btn.getAttribute("aria-busy") !== "true") btn.textContent = installLabel;
    show($("addon-install-hint"), install && !addon.installed);
    setStatus("wow-status", status[0], status[1]);
  }

  function renderSpotify(sp, music) {
    const st = sp.state || "not-set-up";
    const connected = st === "connected" || st === "premium" || st === "allowlist";
    show($("sp-steps"), !connected);
    // Signing in again after the update (D9): the 1.2.0 developer app only needs Web Playback SDK ticked.
    show($("sp-step1-new"), !sp.relogin);
    show($("sp-step1-relogin"), !!sp.relogin);
    show($("sp-connected"), connected);
    show($("sp-waiting"), st === "waiting");
    if (st === "waiting" && sp.login && sp.login.authorizeUrl) $("sp-auth-link").href = sp.login.authorizeUrl;
    if (sp.dashboardUrl) { $("sp-dashboard").href = sp.dashboardUrl; $("sp-dashboard-relogin").href = sp.dashboardUrl; }
    setText($("sp-redirect"), sp.redirectUri || "http://127.0.0.1:8321/callback");
    setVal($("sp-client"), sp.clientId || "");
    setText($("music-order"), ORDER_TEXT[music.order] || ORDER_TEXT["spotify-youtube"]);
    setText($("music-order-hint"), music.fromGame ? "(set in game: Options > AddOns > NeedleDropper)"
                                                  : "(the default: choose it in game, Options > AddOns > NeedleDropper)");
    // A sign-in from before 1.2.1 lacks two permissions in-app Spotify needs (D9): the same form, "Sign in again".
    const signin = $("sp-signin");
    if (signin.getAttribute("aria-busy") !== "true") setText(signin, sp.relogin ? "Sign in again" : "Sign in");
    renderResult($("sp-test-result"), music,
                 [["test-spotify", "Testing: you should hear 15 seconds of Toto – Africa in a few seconds…"]]);

    let note = "", noteCls = "warn";
    if (sp.warning) note = sp.warning;
    else if (st === "premium") {
      note = "Spotify refused to play a song: controlling playback needs Spotify Premium. If you signed in with " +
             "another account than the app's owner, add it under User Management in the Spotify dashboard.";
    } else if (st === "allowlist") {
      note = sp.message || "Spotify refused this account for your app: add it under User Management in the Spotify dashboard.";
      noteCls = "bad";
    } else if (st === "error" && sp.message) { note = sp.message; noteCls = "bad"; }
    else if (sp.reconnect) note = sp.reconnectMessage || "Sign in to Spotify again to grant NeedleDropper one more permission.";
    const msg = $("sp-message");
    msg.className = "callout " + noteCls;
    setText(msg, note);
    show(msg, !!note);

    if (connected) {
      setText($("sp-account"), sp.account || "Signed in");
      const premium = { ok: "✓ Yes: Spotify runs your app (it only does that for a Premium owner).",
                        required: "Required: Spotify refused to play. Playback control needs Spotify Premium.",
                        unknown: "Needed for playback. Spotify checks it when the first song starts." };
      let premiumText = premium[sp.premium] || premium.unknown;
      // With the user-read-private permission Spotify tells the plan itself (real-client finding #3).
      if (sp.product === "premium") premiumText = "✓ Yes: this account has Spotify Premium.";
      else if (sp.product) premiumText = `No: this account is on Spotify ${sp.product.charAt(0).toUpperCase()}${sp.product.slice(1)}. Controlling playback needs Premium.`;
      setText($("sp-premium"), premiumText);
      setText($("sp-country"), sp.country || "");
      show($("sp-country-row"), !!sp.country);
      show($("sp-reconnect-row"), !!sp.reconnect);
      if (!devicesLoaded) loadDevices(false);
      syncDeviceSelect(sp.device || "app", sp.deviceName || "");
    } else {
      devicesLoaded = false;
    }
    if (st === "connected" && lastSpotifyState && lastSpotifyState !== "connected") {
      toast(sp.account ? `Spotify connected: ${sp.account}.` : "Spotify connected.", "ok");
    }
    lastSpotifyState = st;

    const status = { "not-set-up": ["off", "Optional"], waiting: ["wait", "Waiting for Spotify"],
                     connected: ["ok", "Connected"], premium: ["warn", "Premium needed"],
                     allowlist: ["bad", "Account not allowed"], error: ["bad", "Needs attention"] };
    const s = status[st] || status["not-set-up"];
    setStatus("sp-status", s[0], s[1]);
  }

  // "Play on": NeedleDropper itself (spotify.device "app", the default: the Spotify Web Playback SDK in
  // its own player), or another Spotify Connect device ("connect", optionally named by deviceName).
  function deviceValue(device, name) { return device === "connect" ? `connect:${name || ""}` : "app"; }

  function addOption(sel, value, text) {
    const o = document.createElement("option");
    o.value = value;
    o.textContent = text;
    sel.appendChild(o);
    return o;
  }

  function syncDeviceSelect(device, name) {
    const sel = $("sp-device");
    const value = deviceValue(device, name);
    // the hint follows the saved choice, also while the list still has the focus after a change
    setText($("sp-device-hint"), value === "app"
      ? "NeedleDropper plays Spotify itself: no Spotify app has to be open (it needs Google Chrome)."
      : "Remote-controls a separate Spotify app. Open Spotify on that device, then Refresh. For playback here, choose NeedleDropper (this app, recommended).");
    if (document.activeElement === sel) return;
    if (![...sel.options].some((o) => o.value === value)) {
      addOption(sel, value, value === "connect:" ? "The Spotify app on this PC (automatic)" : `${name} (not open right now)`);
    }
    sel.value = value;
  }

  async function loadDevices(announceIt) {
    devicesLoaded = true;
    const sel = $("sp-device");
    const sp = (state && state.spotify) || {};
    try {
      const r = await api("/api/spotify/devices");
      const devices = (r.devices || []).filter((d) => d.name !== APP_DEVICE);
      sel.textContent = "";
      addOption(sel, "app", "NeedleDropper (this app, recommended)");
      addOption(sel, "connect:", "The Spotify app on this PC (automatic)");
      devices.forEach((d) => {
        addOption(sel, `connect:${d.name}`,
                  d.name + (d.type ? ` (${d.type.toLowerCase()})` : "") + (d.isActive ? ", playing now" : ""));
      });
      syncDeviceSelect(r.device || sp.device || "app", r.selected || "");
      if (announceIt) announce(`${devices.length} other Spotify devices found.`);
    } catch (e) {
      syncDeviceSelect(sp.device || "app", sp.deviceName || "");
      if (announceIt) setText($("sp-device-hint"), e.message);
    }
  }

  function renderYouTube(yt, music) {
    const box = $("yt-enabled");
    if (box.getAttribute("aria-busy") !== "true") box.checked = !!yt.enabled;
    setStatus("yt-status", yt.enabled ? "ok" : "off", yt.enabled ? "Ready" : "Off");
    setText($("yt-corner"), CORNER_TEXT[music.corner] || CORNER_TEXT.topright);
    $("yt-test").disabled = !yt.enabled || $("yt-test").getAttribute("aria-busy") === "true";
    $("yt-signin").disabled = yt.player === "none" || $("yt-signin").getAttribute("aria-busy") === "true";
    renderResult($("yt-test-result"), music, [
      ["test-youtube", "Testing: the YouTube box should show in its corner and play Toto – Africa for 15 seconds…"],
      ["youtube-sign-in", "Opening YouTube's sign-in page in the player box's browser…"]]);
  }

  function renderLink(link, wow) {
    const tick = $("link-tick");
    tick.classList.toggle("heard", !!link.heard);
    tick.classList.toggle("idle", !link.heard && !link.listening);
    let line, status;
    const via = TRANSPORT_NAMES[link.transport] ? ` through the ${TRANSPORT_NAMES[link.transport]}` : "";
    if (link.heard) {
      line = `Heard World of Warcraft ${ago(link.heardAgoSec)}${via}. The link works.`;
      status = ["ok", "Linked"];
    } else if (!link.listening) {
      line = "Not listening: both links (pixel and chat log) are switched off in config.json.";
      status = ["bad", "Not listening"];
    } else if (link.seen) {
      line = `World of Warcraft is talking to NeedleDropper (${ago(link.lastSeenAgoSec)}). Type /needle test to finish the check.`;
      status = ["wait", "Waiting for the test"];
    } else {
      line = wow.running ? "Listening for World of Warcraft… Type /needle test in the game."
                         : "Listening for World of Warcraft… Start the game and type /needle test.";
      status = ["wait", "Listening"];
    }
    setText($("link-line"), line);
    const parts = [], details = [];
    Object.keys(TRANSPORT_NAMES).forEach((name) => {
      const t = (link.transports || {})[name];
      if (!t) return;
      const label = TRANSPORT_NAMES[name].replace(/^./, (c) => c.toUpperCase());
      const word = !t.enabled ? "off" : (!t.running ? "not running" : (t.ok ? "receiving" : "waiting"));
      parts.push(`${label}: ${word}`);
      if (t.detail) details.push(`${label}: ${t.detail}`);
    });
    const detail = $("link-detail");
    setText(detail, parts.join(" · "));
    detail.title = details.join("\n");
    if (link.heard && lastHeardCount !== null && link.count !== lastHeardCount) {
      announce("Test signal received: NeedleDropper can hear World of Warcraft.");
    }
    lastHeardCount = link.count || 0;
    setStatus("link-status", status[0], status[1]);
  }

  function renderFooter(app, auto) {
    show($("autostart-row"), auto.available !== false);
    show($("autostart-hint"), auto.available !== false);
    const box = $("autostart");
    if (box.getAttribute("aria-busy") !== "true" && document.activeElement !== box) {
      if (app.setupComplete && auto.choice !== null && auto.choice !== undefined) box.checked = !!auto.enabled;
      else if (!autostartTouched) box.checked = true;
    }
    show($("done-note"), !!app.setupComplete && !app.firstRun);
  }

  // --------------------------------------------------------------- refresh --
  async function refresh() {
    if (refreshing) { refreshAgain = true; return; }
    refreshing = true;
    try {
      apply(await api("/api/state"));
    } catch (e) {
      if (e.network || e.status === 401) {
        offline = !!e.network;
        renderApp(state || {});
        renderBanner(state || {});
      }
    } finally {
      refreshing = false;
      if (refreshAgain) { refreshAgain = false; setTimeout(refresh, 60); }
      schedule();
    }
  }

  function schedule(ms) {
    clearTimeout(pollTimer);
    if (tokenRejected) return; // a new start of NeedleDropper: only a reload can continue
    let delay = ms;
    if (delay == null) {
      const waiting = state && ((state.spotify || {}).state === "waiting" || !(state.link || {}).heard);
      delay = document.visibilityState === "hidden" || offline ? HIDDEN_POLL_MS : (waiting ? FAST_POLL_MS : POLL_MS);
    }
    pollTimer = setTimeout(refresh, delay);
  }

  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") schedule(10); });

  function connectEvents() {
    let es;
    try { es = new EventSource("/events?client=panel"); } catch (e) { return; }
    es.onmessage = (ev) => {
      let cmd = null;
      try { cmd = JSON.parse(ev.data); } catch (e) { return; }
      if (cmd && cmd.cmd === "state") schedule(30);
    };
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) setTimeout(connectEvents, 4000);
    };
  }

  // ---------------------------------------------------------------- events --
  $("wow-change").addEventListener("toggle", () => { if ($("wow-change").open) wowFormOpenedByUser = true; });
  markDirty($("wow-root"));
  $("wow-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const input = $("wow-root");
    const value = input.value.trim();
    if (!value) { fieldError(input, $("wow-error"), "Type the folder that contains _forever_."); return; }
    busy($("wow-save"), "Saving…", async () => {
      try {
        await saveConfig({ wowRoot: value });
        fieldError(input, $("wow-error"), "");
        clean(input);
        if (state && state.wow && state.wow.found) { $("wow-change").open = false; toast("World of Warcraft folder saved.", "ok"); }
      } catch (e) {
        if (e.data && e.data.fields && e.data.fields.wowRoot) fieldError(input, $("wow-error"), e.message);
        else throw e;
      }
    });
  });

  $("wow-detect").addEventListener("click", () => busy($("wow-detect"), "Looking…", async () => {
    const r = await api("/api/wow/detect", { method: "POST", body: {} });
    clean($("wow-root"));
    toast(r.message, r.found ? "ok" : "error");
    await refresh();
  }));

  async function install(force) {
    const r = await api("/api/addon/install", { method: "POST", body: { force } });
    show($("addon-confirm"), false);
    toast(r.message || "NeedleDropper addon installed.", "ok");
    await refresh();
  }

  $("addon-install").addEventListener("click", () => busy($("addon-install"), "Installing…", async () => {
    try {
      await install(false);
    } catch (e) {
      if (e.status === 409 && e.data && e.data.gameRunning) {
        setText($("addon-confirm-text"), e.message);
        show($("addon-confirm"), true);
        $("addon-force").focus();
      } else {
        throw e;
      }
    }
  }));
  $("addon-force").addEventListener("click", () => busy($("addon-force"), "Installing…", () => install(true)));
  $("addon-cancel").addEventListener("click", () => { show($("addon-confirm"), false); $("addon-install").focus(); });

  $("sp-copy").addEventListener("click", async () => {
    const copied = await copyText($("sp-redirect").textContent, $("sp-redirect"));
    toast(copied ? "Redirect URI copied." : "Selected: press Ctrl+C to copy.", "ok");
  });

  markDirty($("sp-client"));
  $("sp-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const input = $("sp-client");
    const cid = input.value.trim();
    if (!cid) { fieldError(input, $("sp-error"), "Paste the Client ID from your Spotify app's settings."); input.focus(); return; }
    if (!/^[A-Za-z0-9]{8,64}$/.test(cid)) {
      fieldError(input, $("sp-error"), "That is not a Client ID: it is 32 letters and digits from your app's Settings.");
      input.focus();
      return;
    }
    fieldError(input, $("sp-error"), "");
    // Open the tab now, inside the click: a tab opened after the request would be blocked.
    let tab = null;
    try {
      tab = window.open("", "_blank");
      if (tab) {
        tab.opener = null;
        tab.document.title = "Connecting to Spotify…";
        tab.document.body.style.cssText = "background:#07070a;color:#f2ede0;font:15px 'Segoe UI',sans-serif;padding:40px";
        tab.document.body.textContent = "Opening Spotify…";
      }
    } catch (e) { /* the tab still navigates below */ }
    await busy($("sp-signin"), "Opening Spotify…", async () => {
      try {
        const r = await api("/api/spotify/connect", { method: "POST", body: { clientId: cid } });
        clean(input);
        $("sp-auth-link").href = r.authorizeUrl;
        if (tab && !tab.closed) tab.location.href = r.authorizeUrl;
        else toast("Your browser blocked the new tab: use “Open Spotify's sign-in page again”.", "error");
        await refresh();
      } catch (e) {
        if (tab && !tab.closed) tab.close();
        if (e.data && e.data.fields && e.data.fields["spotify.clientId"]) fieldError(input, $("sp-error"), e.message);
        else throw e;
      }
    });
  });

  // "Sign in again" (a sign-in from an older version lacks a permission): the same flow as Sign in,
  // inside this click so the browser lets it open Spotify's tab.
  $("sp-reconnect").addEventListener("click", () => { $("sp-form").requestSubmit(); });
  $("sp-refresh").addEventListener("click", () => busy($("sp-refresh"), "Looking…", () => loadDevices(true)));
  $("sp-device").addEventListener("change", async () => {
    const value = $("sp-device").value;
    const name = value.startsWith("connect:") ? value.slice("connect:".length) : "";
    const patch = value === "app" ? { spotify: { device: "app" } } : { spotify: { device: "connect", deviceName: name } };
    const msg = value === "app" ? "Spotify songs will play in NeedleDropper itself."
      : (name ? `Spotify songs will play on ${name}.` : "Spotify songs will play in the Spotify app on this PC.");
    try {
      await saveConfig(patch, msg);
    } catch (e) { toast(e.message, "error"); }
  });
  $("sp-test").addEventListener("click", () => sendRequest($("sp-test"), "/api/music/test", { service: "spotify" },
                                                           "test-spotify", "Testing…"));
  $("yt-test").addEventListener("click", () => sendRequest($("yt-test"), "/api/music/test", { service: "youtube" },
                                                           "test-youtube", "Testing…"));
  $("yt-signin").addEventListener("click", () => sendRequest($("yt-signin"), "/api/youtube/signin", {},
                                                             "youtube-sign-in", "Opening…"));
  $("sp-signout").addEventListener("click", () => busy($("sp-signout"), "Signing out…", async () => {
    await api("/api/spotify/disconnect", { method: "POST", body: {} });
    toast("Signed out of Spotify.", "ok");
    await refresh();
  }));

  $("yt-enabled").addEventListener("change", async () => {
    const box = $("yt-enabled");
    box.setAttribute("aria-busy", "true");
    try {
      await saveConfig({ youtube: { enabled: box.checked } }, box.checked ? "YouTube songs are on." : "YouTube songs are off.");
    } catch (e) {
      toast(e.message, "error");
      box.checked = !box.checked;
    } finally {
      box.removeAttribute("aria-busy");
    }
  });

  $("autostart").addEventListener("change", async () => {
    autostartTouched = true;
    const box = $("autostart");
    if (!(state && state.app && state.app.setupComplete && !state.app.firstRun)) return; // applied by Done
    box.setAttribute("aria-busy", "true");
    try {
      await saveConfig({ startWithWindows: box.checked },
                       box.checked ? "NeedleDropper will start with Windows." : "NeedleDropper will not start with Windows.");
    } catch (e) {
      toast(e.message, "error");
    } finally {
      box.removeAttribute("aria-busy");
    }
  });

  $("done").addEventListener("click", () => busy($("done"), "Saving…", async () => {
    // The first Done records the Start with Windows choice (ticked by default); where Windows'
    // autostart is not available the choice is simply "off".
    const available = !(state && state.autostart && state.autostart.available === false);
    await saveConfig({ setupComplete: true, startWithWindows: available && $("autostart").checked });
    const note = $("done-note");
    show(note, true);
    note.setAttribute("tabindex", "-1");
    note.focus();
    announce(note.textContent);
  }));

  async function openLogs(btn) {
    await busy(btn, null, async () => {
      const r = await api("/api/open", { method: "POST", body: { what: "logs" } });
      toast(`Opened ${r.path}.`, "ok");
    });
  }
  $("open-logs").addEventListener("click", () => openLogs($("open-logs")));
  $("tips-logs").addEventListener("click", () => openLogs($("tips-logs")));
  $("banner-logs").addEventListener("click", () => openLogs($("banner-logs")));
  $("banner-reload").addEventListener("click", () => window.location.reload());

  // ------------------------------------------------------------------ start --
  refresh();
  connectEvents();
})();
