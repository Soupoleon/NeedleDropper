/* NeedleDropper player window.
 *
 * Receives commands from the companion over Server-Sent Events
 * (/events?client=player) and reports playback state with POST /status
 * (header X-ND-Token, read from <meta name="nd-token">):
 *   {cmd:"show", dropId, label, title, artist, artUrl, attribution, link, hint}
 *   {cmd:"hide", dropId}
 *   {cmd:"play", playId, source:"local"|"youtube", url|videoId, startMs, fadeInMs, volume}
 *   {cmd:"cue", videoId, startMs, dropId}   (ARM: load the video paused and silent; FIRE plays it)
 *   {cmd:"stop", playId, fadeOutMs}
 *   {cmd:"fade", playId, volume, ms}
 * Status: {state:"ready"|"playing"|"ended"|"error"|"autoplay-blocked", detail, playId, code?}
 *
 * YouTube's rules: the video stays visible (>= 320x180) and uncovered while it plays. The app keeps
 * this window hidden between songs and shows it on top while one plays; the page never focuses itself.
 */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var body = document.body;
  var audio = $("audio");
  var TOKEN = (document.querySelector('meta[name="nd-token"]') || {}).content || "";

  var unlocked = false;      // a user gesture happened (or autoplay is allowed)
  var pending = null;        // play command blocked by autoplay policy
  var current = null;        // {playId, source, volume}
  var lastCard = null;       // the last title card shown (for "Drop finished")
  var audioGen = 0;          // bumps on every new local play (guards stale callbacks)
  var fades = { audio: null, yt: null };
  var hideTimer = null;
  var es = null;
  var retryMs = 1000;
  var yt = { player: null, ready: false, loading: false, waiting: [], watch: null };
  var cued = null;           // {videoId, startMs}: cued on ARM (paused, silent), started by the FIRE

  // ------------------------------------------------------------ status --
  function post(state, detail, playId, extra) {
    var payload = { state: state, detail: detail || "" };
    if (playId) payload.playId = playId;
    if (extra) for (var k in extra) if (Object.prototype.hasOwnProperty.call(extra, k)) payload[k] = extra[k];
    try {
      fetch("/status", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-ND-Token": TOKEN },
        body: JSON.stringify(payload),
        keepalive: true,
        credentials: "same-origin"
      }).catch(function () {});
    } catch (e) { /* offline: nothing to do */ }
  }

  function setConn(ok, text) {
    $("conn-dot").className = ok ? "" : "off";
    $("conn-text").textContent = text;
  }

  // ------------------------------------------------------------- fades --
  function clamp01(v) { return Math.max(0, Math.min(1, v)); }

  function fade(channel, get, set, to, ms, done) {
    if (fades[channel]) { clearInterval(fades[channel]); fades[channel] = null; }
    if (!ms || ms <= 0) { set(to); if (done) done(); return; }
    var from = get();
    var t0 = performance.now();
    fades[channel] = setInterval(function () {
      var k = Math.min(1, (performance.now() - t0) / ms);
      set(from + (to - from) * k);
      if (k >= 1) {
        clearInterval(fades[channel]); fades[channel] = null;
        if (done) done();
      }
    }, 40);
  }

  function cancelFade(channel) {
    if (fades[channel]) { clearInterval(fades[channel]); fades[channel] = null; }
  }

  function targetVolume(cmd) {
    var v = (cmd && typeof cmd.volume === "number") ? cmd.volume : 100;
    return Math.max(0, Math.min(100, v));
  }

  // -------------------------------------------------------------- card --
  function showCard(c) {
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    var card = $("card");
    var same = card.getAttribute("data-drop") === (c.dropId || "") && body.classList.contains("showing");
    lastCard = c;

    $("label").textContent = c.label || "";
    $("artist").textContent = c.artist || "";
    $("title").textContent = c.title || "";
    $("dot").style.display = (c.artist && c.title) ? "" : "none";

    var art = $("art"), wrap = $("art-wrap");
    if (c.artUrl) {
      if (art.getAttribute("src") !== c.artUrl) art.src = c.artUrl;
      art.onerror = function () { wrap.hidden = true; };
      wrap.hidden = false;
    } else {
      wrap.hidden = true;
      art.removeAttribute("src");
    }

    var attr = $("attribution"), link = $("link"), attrText = $("attr-text");
    if (c.attribution) {
      var text = "Playing on " + c.attribution;
      if (c.link) {
        link.textContent = text; link.href = c.link; link.style.display = "";
        attrText.textContent = "";
      } else {
        link.style.display = "none"; link.removeAttribute("href");
        attrText.textContent = text;
      }
      attr.hidden = false;
    } else {
      attr.hidden = true;
    }
    $("hint").textContent = c.hint || "";
    card.setAttribute("data-drop", c.dropId || "");
    document.title = c.label ? (c.label + " — NeedleDropper") : "NeedleDropper";

    if (!same) {
      body.classList.remove("visible");
      body.classList.add("showing");
      body.classList.remove("idle");
      void card.offsetWidth; // restart the fade-in transition
      requestAnimationFrame(function () { body.classList.add("visible"); });
      setTimeout(function () { body.classList.add("visible"); }, 60); // rAF is paused in hidden windows
    } else {
      body.classList.add("showing", "visible");
    }
  }

  function showFinished() {
    var c = lastCard;
    $("idle-title").hidden = !c;
    var last = $("idle-last");
    if (c) {
      last.textContent = (c.label || "") + (c.artist || c.title ? " — " + [c.artist, c.title].filter(Boolean).join(" · ") : "");
      last.hidden = !last.textContent;
      $("idle-line").textContent = "The next YouTube song plays here.";
      $("idle-help").textContent = "NeedleDropper hides this window between songs.";
    }
  }

  function hideCard(c) {
    var card = $("card");
    if (c && c.dropId && card.getAttribute("data-drop") && card.getAttribute("data-drop") !== c.dropId) return;
    body.classList.remove("visible");
    if (hideTimer) clearTimeout(hideTimer);
    hideTimer = setTimeout(function () {
      body.classList.remove("showing");
      body.classList.add("idle");
      card.setAttribute("data-drop", "");
      document.title = "Drop finished — NeedleDropper";
      showFinished();
      hideTimer = null;
    }, 2100);
  }

  // ------------------------------------------------------- local audio --
  function stopLocalNow() {
    cancelFade("audio");
    audioGen++;
    try { audio.pause(); } catch (e) {}
    audio.removeAttribute("src");
    try { audio.load(); } catch (e) {}
  }

  function playLocal(cmd) {
    if (current && current.source === "youtube") stopYoutubeNow();
    stopLocalNow();
    var gen = audioGen;
    var vol = targetVolume(cmd) / 100;
    current = { playId: cmd.playId, source: "local", volume: vol * 100 };
    var startSec = Math.max(0, (cmd.startMs || 0) / 1000);
    audio.volume = (cmd.fadeInMs > 0) ? 0 : vol;
    audio.src = cmd.url;

    var begin = function () {
      if (gen !== audioGen) return;
      try { if (startSec > 0 && isFinite(audio.duration) && startSec < audio.duration) audio.currentTime = startSec; }
      catch (e) { /* seeking unsupported: start from the top */ }
      var p = audio.play();
      var ok = function () {
        if (gen !== audioGen) return;
        pending = null;
        hideUnlock();
        post("playing", "local", cmd.playId);
        fade("audio", function () { return audio.volume; }, function (v) { audio.volume = clamp01(v); },
             vol, cmd.fadeInMs || 0);
      };
      var fail = function (err) {
        if (gen !== audioGen) return;
        if (err && err.name === "NotAllowedError") {
          pending = cmd;
          showUnlock();
          post("autoplay-blocked", "click the NeedleDropper player window to enable sound", cmd.playId);
        } else {
          post("error", "local: " + (err && err.message ? err.message : "playback failed"), cmd.playId);
          if (current && current.playId === cmd.playId) current = null;
        }
      };
      if (p && typeof p.then === "function") p.then(ok, fail); else ok();
    };
    if (audio.readyState >= 1) begin();
    else audio.addEventListener("loadedmetadata", begin, { once: true });
  }

  audio.addEventListener("ended", function () {
    if (current && current.source === "local") {
      var id = current.playId; current = null;
      post("ended", "local", id);
    }
  });
  audio.addEventListener("error", function () {
    if (!audio.getAttribute("src")) return;
    if (current && current.source === "local") {
      var code = audio.error ? audio.error.code : 0;
      var names = { 1: "aborted", 2: "network error", 3: "decode error", 4: "file format not supported" };
      var id = current.playId; current = null;
      post("error", "local: " + (names[code] || "media error"), id, { code: code });
    }
  });

  // ----------------------------------------------------------- YouTube --
  var YT_ERRORS = {
    2: "invalid video id",
    5: "HTML5 player error",
    100: "video not found or private",
    101: "the owner does not allow embedding",
    150: "the owner does not allow embedding",
    153: "missing referrer/origin (embed rejected)"
  };

  function showYt() { body.classList.add("yt-on"); }
  function hideYt() { body.classList.remove("yt-on"); }

  function ytVolume() {
    try { return yt.player && yt.ready ? yt.player.getVolume() / 100 : 0; } catch (e) { return 0; }
  }
  function ytSetVolume(v) {
    try { if (yt.player && yt.ready) yt.player.setVolume(Math.round(clamp01(v) * 100)); } catch (e) {}
  }

  function stopYoutubeNow() {
    cancelFade("yt");
    if (yt.watch) { clearTimeout(yt.watch); yt.watch = null; }
    try { if (yt.player && yt.ready) yt.player.stopVideo(); } catch (e) {}
    hideYt();
  }

  function withYouTube(cb) {
    if (yt.player && yt.ready) { cb(); return; }
    yt.waiting.push(cb);
    if (yt.loading) return;
    yt.loading = true;
    var create = function () {
      yt.player = new YT.Player("yt", {
        width: "100%",
        height: "100%",
        playerVars: {
          autoplay: 0, controls: 1, rel: 0, playsinline: 1, fs: 0, iv_load_policy: 3,
          enablejsapi: 1, origin: location.origin
        },
        events: {
          onReady: function () {
            yt.ready = true;
            var q = yt.waiting; yt.waiting = [];
            q.forEach(function (f) { f(); });
          },
          onStateChange: onYtState,
          onError: onYtError
        }
      });
    };
    if (window.YT && window.YT.Player) { create(); return; }
    var prev = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = function () {
      if (typeof prev === "function") { try { prev(); } catch (e) {} }
      create();
    };
    var s = document.createElement("script");
    s.src = "https://www.youtube.com/iframe_api";
    s.async = true;
    s.onerror = function () {
      yt.loading = false;
      yt.waiting = [];
      var id = current && current.source === "youtube" ? current.playId : null;
      if (id) current = null;
      hideYt();
      post("error", "youtube: could not load the YouTube player (offline or blocked?)", id, { code: -1 });
    };
    document.head.appendChild(s);
  }

  // ARM, ~10 s before the FIRE: cue the video (loaded, paused, silent) so the FIRE only starts it.
  // cueVideoById never plays; a warmer start (load, then pause at the first frame) would play a few
  // milliseconds of video in a hidden window, a grey area of YouTube's terms (research 1.3 / 3.3).
  function cueYoutube(cmd) {
    if (!cmd.videoId) return;
    if (current && current.source === "youtube") return;   // a song plays: never disturb it
    withYouTube(function () {
      if (current && current.source === "youtube") return;
      try {
        yt.player.cueVideoById({ videoId: cmd.videoId, startSeconds: Math.floor((cmd.startMs || 0) / 1000) });
        cued = { videoId: cmd.videoId, startMs: cmd.startMs || 0 };
      } catch (e) { cued = null; }
    });
  }

  function playYoutube(cmd) {
    if (current && current.source === "local") stopLocalNow();
    var wasCued = cued && cued.videoId === cmd.videoId && (cued.startMs | 0) === (cmd.startMs | 0);
    if (!wasCued) stopYoutubeNow();
    else { cancelFade("yt"); if (yt.watch) { clearTimeout(yt.watch); yt.watch = null; } }
    cued = null;
    var vol = targetVolume(cmd) / 100;
    current = { playId: cmd.playId, source: "youtube", volume: vol * 100, started: false, cmd: cmd };
    showYt();
    withYouTube(function () {
      if (!current || current.playId !== cmd.playId) return;
      ytSetVolume(cmd.fadeInMs > 0 ? 0 : vol);
      try {
        if (wasCued) yt.player.playVideo();
        else yt.player.loadVideoById({ videoId: cmd.videoId, startSeconds: Math.floor((cmd.startMs || 0) / 1000) });
      } catch (e) {
        post("error", "youtube: " + e.message, cmd.playId, { code: -2 });
        current = null; hideYt();
        return;
      }
      // Autoplay with sound needs a prior user gesture; detect a player that never starts.
      if (yt.watch) clearTimeout(yt.watch);
      yt.watch = setTimeout(function () {
        yt.watch = null;
        if (!current || current.playId !== cmd.playId || current.started) return;
        var st = -1;
        try { st = yt.player.getPlayerState(); } catch (e) {}
        if (st === -1 || st === 5 || st === 2) {
          pending = cmd;
          showUnlock();
          post("autoplay-blocked", "click the NeedleDropper player window to enable sound", cmd.playId);
        }
      }, 4000);
    });
  }

  function onYtState(e) {
    if (!current || current.source !== "youtube") return;
    var S = window.YT && YT.PlayerState ? YT.PlayerState : { PLAYING: 1, ENDED: 0 };
    if (e.data === S.PLAYING && !current.started) {
      current.started = true;
      pending = null;
      unlocked = true;
      hideUnlock();
      post("playing", "youtube", current.playId);
      var vol = current.volume / 100;
      fade("yt", ytVolume, ytSetVolume, vol, current.cmd.fadeInMs || 0);
    } else if (e.data === S.ENDED) {
      var id = current.playId; current = null;
      hideYt();
      post("ended", "youtube", id);
    }
  }

  function onYtError(e) {
    var code = e && typeof e.data === "number" ? e.data : 0;
    var id = current && current.source === "youtube" ? current.playId : null;
    if (id) current = null;
    if (yt.watch) { clearTimeout(yt.watch); yt.watch = null; }
    hideYt();
    post("error", "youtube " + code + ": " + (YT_ERRORS[code] || "player error"), id, { code: code });
  }

  // -------------------------------------------------------- stop / fade --
  function stopCmd(cmd) {
    if (!current) { pending = null; hideUnlock(); return; }
    if (cmd.playId && current.playId !== cmd.playId) return;
    var c = current; current = null; pending = null;
    hideUnlock();
    var ms = cmd.fadeOutMs || 0;
    if (c.source === "local") {
      var gen = audioGen;
      fade("audio", function () { return audio.volume; }, function (v) { audio.volume = clamp01(v); }, 0, ms,
        function () {
          if (gen === audioGen) { try { audio.pause(); } catch (e) {} }
          post("ended", "stopped", c.playId);
        });
    } else if (c.source === "youtube") {
      if (yt.watch) { clearTimeout(yt.watch); yt.watch = null; }
      fade("yt", ytVolume, ytSetVolume, 0, ms, function () {
        if (!current || current.source !== "youtube") {
          try { if (yt.player && yt.ready) yt.player.stopVideo(); } catch (e) {}
          hideYt();
        }
        post("ended", "stopped", c.playId);
      });
    }
  }

  function fadeCmd(cmd) {
    if (!current || (cmd.playId && current.playId !== cmd.playId)) return;
    var to = targetVolume(cmd) / 100;
    current.volume = to * 100;
    if (current.source === "local") {
      fade("audio", function () { return audio.volume; }, function (v) { audio.volume = clamp01(v); }, to, cmd.ms || 0);
    } else {
      fade("yt", ytVolume, ytSetVolume, to, cmd.ms || 0);
    }
  }

  // ------------------------------------------------------------ unlock --
  function showUnlock() { $("unlock").hidden = false; }
  function hideUnlock() { $("unlock").hidden = true; }

  function unlockNow() {
    unlocked = true;
    hideUnlock();
    if (es && es.readyState === 1) setConn(true, "Connected");
    var p = pending; pending = null;
    if (!p) return;
    if (current && current.playId === p.playId && current.source === "local") {
      var vol = targetVolume(p) / 100;
      audio.play().then(function () {
        post("playing", "local (after click)", p.playId);
        fade("audio", function () { return audio.volume; }, function (v) { audio.volume = clamp01(v); }, vol, p.fadeInMs || 0);
      }, function (err) { post("error", "local: " + (err && err.message), p.playId); });
    } else if (current && current.playId === p.playId && current.source === "youtube") {
      try { yt.player.playVideo(); } catch (e) {}
    } else if (!current) {
      handle(p);
    }
  }

  function initUnlock() {
    var allowed = false;
    try {
      if (navigator.getAutoplayPolicy) allowed = navigator.getAutoplayPolicy("mediaelement") === "allowed";
      else if (navigator.userActivation && navigator.userActivation.hasBeenActive) allowed = true;
      else {
        var AC = window.AudioContext || window.webkitAudioContext;
        if (AC) { var ctx = new AC(); allowed = ctx.state === "running"; ctx.close(); }
      }
    } catch (e) { allowed = false; }
    unlocked = allowed;
    var onGesture = function () { if (!unlocked || pending) unlockNow(); };
    ["pointerdown", "keydown", "touchstart"].forEach(function (name) {
      document.addEventListener(name, onGesture, { capture: true });
    });
    $("unlock").addEventListener("click", unlockNow);
  }

  // ---------------------------------------------------------- commands --
  function handle(cmd) {
    if (!cmd || typeof cmd.cmd !== "string") return;
    switch (cmd.cmd) {
      case "show": showCard(cmd); break;
      case "hide": hideCard(cmd); break;
      case "play":
        if (!unlocked) showUnlock();
        if (cmd.source === "youtube") playYoutube(cmd); else playLocal(cmd);
        break;
      case "cue": cueYoutube(cmd); break;
      case "stop": stopCmd(cmd); break;
      case "fade": fadeCmd(cmd); break;
      default: break;
    }
  }

  function connect() {
    try { if (es) es.close(); } catch (e) {}
    es = new EventSource("/events?client=player");
    es.onopen = function () {
      retryMs = 1000;
      setConn(true, unlocked ? "Connected" : "Connected — click once to enable sound");
      post("ready", unlocked ? "sound enabled" : "waiting for a click to enable sound");
    };
    es.onmessage = function (e) {
      var cmd;
      try { cmd = JSON.parse(e.data); } catch (err) { return; }
      handle(cmd);
    };
    es.onerror = function () {
      setConn(false, "Companion not reachable — retrying…");
      if (es.readyState === EventSource.CLOSED) {
        setTimeout(connect, retryMs);
        retryMs = Math.min(retryMs * 2, 15000);
      }
    };
  }

  initUnlock();
  connect();
})();
