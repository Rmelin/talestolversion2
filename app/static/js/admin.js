/* Kontrolpanel. Kontrolsessionens id ligger i sessionStorage (pr. fane), så en
 * genindlæsning af fanen kan fortsætte, mens en ny fane/maskine må overtage. */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var esc = Tal.esc;

  var S = {
    state: null, scenes: [], uploads: [], quick: [],
    selectedId: null, draft: null, editorDirty: false,
    quickDirty: {}, sessionId: null, myAcquiredAt: null, heartbeatOk: false, lastBeatAt: null,
    dismissedReason: null
  };

  var SESSION_KEY = "talerstol-control-session";
  var LABEL_KEY = "talerstol-label";

  // ------------------------------------------------------------ kontrolsession + heartbeat

  var worker = new Worker("/static/js/heartbeat-worker.js");
  worker.onmessage = function (ev) {
    var m = ev.data;
    if (m.session !== S.sessionId) return;
    if (m.type === "ok") {
      var wasOk = S.heartbeatOk;
      S.heartbeatOk = true; S.lastBeatAt = Date.now();
      if (m.data && m.data.control) { mergeControl(m.data.control); }
      if (!wasOk) renderControl();
    } else if (m.type === "rejected") {
      if (m.status === 409 || m.status === 401) {
        lostControl((m.data && m.data.detail) || "Kontrolsessionen er ikke længere gyldig.");
      }
    } else {
      S.heartbeatOk = false; renderControl();
    }
  };

  function label() {
    var l = "";
    try { l = localStorage.getItem(LABEL_KEY) || ""; } catch (e) {}
    if (!l) {
      var p = navigator.platform || "";
      l = /Mac/i.test(p) ? "Mac" : /Win/i.test(p) ? "Windows-pc" : /Linux/i.test(p) ? "Linux-pc" : "Kontrolpanel";
      l += " " + Math.random().toString(36).slice(2, 6).toUpperCase();
      try { localStorage.setItem(LABEL_KEY, l); } catch (e) {}
    }
    return l;
  }

  function setSession(id, acquiredAt) {
    S.sessionId = id; S.myAcquiredAt = acquiredAt || null;
    Tal.headers["X-Control-Session"] = id || null;
    try {
      if (id) sessionStorage.setItem(SESSION_KEY, JSON.stringify({ id: id, at: acquiredAt }));
      else sessionStorage.removeItem(SESSION_KEY);
    } catch (e) {}
    if (id) worker.postMessage({ cmd: "start", sessionId: id, interval: interval() });
    else worker.postMessage({ cmd: "stop" });
  }

  function interval() {
    return (S.state && S.state.control && S.state.control.timing.heartbeat_interval) || 10;
  }

  function haveControl() { return !!S.sessionId; }

  function lostControl(reason) {
    if (!S.sessionId) return;
    setSession(null);
    S.heartbeatOk = false;
    showAlert("Du har ikke længere kontrollen: " + reason);
    renderControl();
  }

  function acquire(btn, force) {
    var path = force ? "/api/control/takeover" : "/api/control/acquire";
    return Tal.run(btn, function () {
      return Tal.api("POST", path, { label: label() }).then(function (res) {
        applyState(res.state);
        setSession(res.session_id, res.state.control.holder && res.state.control.holder.acquired_at);
        S.heartbeatOk = true; S.lastBeatAt = Date.now();
        hideAlert();
        renderAll();
        Tal.toast("Du har kontrollen. Live-scenen er uændret.", "ok");
      }).catch(function (err) {
        if (err.code === "control_busy" && !force) {
          var holder = S.state && S.state.control.holder;
          if (confirm("Kontrollen holdes af '" + (holder ? holder.label : "en anden") +
                      "'.\n\nVil du overtage kontrollen? Den anden maskine kan derefter ikke længere publicere scener.")) {
            return Tal.api("POST", "/api/control/takeover", { label: label() }).then(function (res) {
              applyState(res.state);
              setSession(res.session_id, res.state.control.holder && res.state.control.holder.acquired_at);
              S.heartbeatOk = true; S.lastBeatAt = Date.now();
              hideAlert(); renderAll();
              Tal.toast("Du har overtaget kontrollen.", "ok");
            });
          }
          return;
        }
        throw err;
      });
    });
  }

  // ------------------------------------------------------------ tilstand fra server

  function applyState(snap) {
    if (!snap || snap.type !== "state") return;
    if (S.state && S.state.boot_id === snap.boot_id && snap.rev < S.state.rev) return;
    var bootChanged = S.state && S.state.boot_id !== snap.boot_id;
    S.state = snap;
    if (bootChanged && S.sessionId) lostControl("Talerstolen er genstartet. Fallback vises.");
    checkHolder();
    worker.postMessage({ cmd: "interval", interval: interval() });
  }

  function mergeControl(control) {
    if (!S.state) return;
    S.state.control = control;
    checkHolder();
  }

  // Mister vi kontrollen (overtagelse/timeout), kan vi se det på holderen.
  function checkHolder() {
    if (!S.sessionId || !S.state) return;
    var h = S.state.control.holder;
    if (!h) { worker.postMessage({ cmd: "beat" }); return; }
    if (S.myAcquiredAt && h.acquired_at !== S.myAcquiredAt) worker.postMessage({ cmd: "beat" });
  }

  function onMessage(msg) {
    if (msg.type === "state") { applyState(msg); renderAll(); }
    else if (msg.type === "control") {
      if (S.state) { S.state.displays = msg.displays; mergeControl(msg.control); renderControl(); }
    } else if (msg.type === "library") {
      if (msg.what === "scenes") loadScenes();
      else if (msg.what === "uploads") loadUploads();
      else if (msg.what === "quicktexts") loadQuick(true);
    }
  }

  // ------------------------------------------------------------ render: status og knapper

  function showAlert(text) { $("alert-text").textContent = text; $("alert").hidden = false; }
  function hideAlert() { $("alert").hidden = true; }
  $("alert-close").onclick = function () {
    hideAlert();
    if (S.state) S.dismissedReason = S.state.control.reason_at;
  };

  function renderControl() {
    if (!S.state) return;
    var c = S.state.control, h = c.holder;
    var html = Tal.stateBadge(c);
    var who = h ? (haveControl() ? "<strong>Du</strong> (" + esc(h.label) + ")" : esc(h.label)) : "Ingen";
    html += '<span class="status-meta">Kontrol: ' + who + "</span>";
    html += '<span class="status-meta">Seneste heartbeat: ' + Tal.fmtClock(c.last_heartbeat_at) +
      (c.seconds_since_heartbeat != null ? " (" + Tal.fmtSecs(c.seconds_since_heartbeat) + " siden)" : "") + "</span>";
    if (c.seconds_to_fallback != null && c.state !== "CONTROL_RELEASED") {
      html += '<span class="status-meta">Fallback om: ' + Tal.fmtSecs(c.seconds_to_fallback) + "</span>";
    } else if (!c.timing.auto_fallback) {
      html += '<span class="status-meta warn-text">Automatisk fallback er slået FRA</span>';
    }
    html += '<span class="status-meta">Displays: ' + (S.state.displays || 0) + "</span>";
    if (haveControl() && !S.heartbeatOk) html += '<span class="status-meta warn-text">Heartbeat når ikke frem!</span>';
    $("status").innerHTML = html;

    var mine = haveControl();
    $("btn-acquire").hidden = mine;
    $("btn-acquire").textContent = h ? "Overtag kontrol fra " + h.label : "Overtag kontrol";
    $("btn-release").hidden = !mine;
    document.body.classList.toggle("has-control", mine);

    // Vis årsagen til fallback, når forbindelsen er tilbage.
    if ((c.state === "FALLBACK_ACTIVE" || c.state === "CONTROL_WARNING") && S.dismissedReason !== c.reason_at) {
      if ($("alert").hidden || $("alert-text").textContent.indexOf("Du har ikke længere") !== 0) {
        showAlert(Tal.fmtClock(c.reason_at) + " – " + c.reason);
      }
    }
    renderVis();
  }

  function renderVis() {
    var p = S.state && S.state.preview;
    var btn = $("btn-vis"), hint = $("vis-hint");
    btn.disabled = !haveControl() || !p;
    if (!haveControl()) hint.textContent = "Overtag kontrol for at publicere.";
    else if (!p) hint.textContent = "Klargør en scene først.";
    else hint.textContent = "Viser '" + p.name + "'" + (p.content.primary_text ? " – " + p.content.primary_text : "");
  }

  // ------------------------------------------------------------ render: preview / live

  var lastPreviewKey = null, lastLiveKey = null;
  function renderStages() {
    var st = S.state;
    if (!st) return;
    var canvas = st.canvas;
    document.documentElement.style.setProperty("--canvas-ratio", canvas.width + "/" + canvas.height);
    var pk = st.boot_id + ":" + (st.preview ? st.preview.rev : "none") + ":" + canvas.width;
    if (pk !== lastPreviewKey) {
      lastPreviewKey = pk;
      if (st.preview) {
        SceneRender.mount($("preview-stage"), st.preview.content, canvas);
        $("preview-name").textContent = st.preview.name;
      } else {
        $("preview-stage").innerHTML = '<div class="empty">Ingen scene klargjort</div>';
        $("preview-name").textContent = "";
      }
    }
    var lk = st.boot_id + ":" + (st.live ? st.live.rev : "none") + ":" + canvas.width;
    if (lk !== lastLiveKey && st.live) {
      lastLiveKey = lk;
      SceneRender.mount($("live-stage"), st.live.content, canvas);
      $("live-name").innerHTML = esc(st.live.name) + (st.live.is_fallback ? ' <span class="tag tag-bad">FALLBACK</span>' : "") +
        ' <span class="muted small">' + Tal.fmtClock(st.live.at) + "</span>";
    }
  }

  // ------------------------------------------------------------ sceneliste

  function loadScenes() {
    return Tal.api("GET", "/api/scenes").then(function (r) {
      S.scenes = r.scenes;
      renderScenes();
      if (S.selectedId && !sceneById(S.selectedId)) selectScene(null, true);
      else if (S.selectedId && !S.editorDirty) loadEditor(sceneById(S.selectedId));
      else if (!S.selectedId && S.scenes.length) selectScene(S.scenes[0].id, true, true);
    });
  }

  function sceneById(id) {
    for (var i = 0; i < S.scenes.length; i++) if (S.scenes[i].id === id) return S.scenes[i];
    return null;
  }
  function uploadById(id) {
    for (var i = 0; i < S.uploads.length; i++) if (S.uploads[i].id === id) return S.uploads[i];
    return null;
  }

  // Byg visningsindhold for en scene ud fra listen (til thumbnails og kladde).
  function contentOf(scene) {
    var c = {};
    for (var k in scene) c[k] = scene[k];
    c.bg_image = scene.bg_image_id ? uploadById(scene.bg_image_id) : null;
    c.logo_image = scene.logo_image_id ? uploadById(scene.logo_image_id) : null;
    return c;
  }

  function renderScenes() {
    var ul = $("scene-list");
    var st = S.state || {};
    var liveId = st.live && st.live.scene_id, prevId = st.preview && st.preview.scene_id;
    var fbId = st.settings && st.settings.fallback_scene_id;
    ul.innerHTML = "";
    S.scenes.forEach(function (sc, i) {
      var li = document.createElement("li");
      li.className = "scene-item" + (sc.id === S.selectedId ? " selected" : "");
      li.dataset.id = sc.id;
      li.innerHTML =
        '<button type="button" class="scene-pick" title="Indlæs i preview">' +
        '<div class="scene-thumb"></div><div class="scene-info"><div class="scene-name">' + esc(sc.name) + "</div>" +
        '<div class="scene-sub">' + esc(sc.primary_text || "") + "</div><div class='scene-tags'>" +
        (sc.id === liveId ? '<span class="tag tag-live">LIVE</span>' : "") +
        (sc.id === prevId ? '<span class="tag tag-prev">PREVIEW</span>' : "") +
        (sc.id === fbId ? '<span class="tag">Fallback</span>' : "") +
        "</div></div></button>" +
        '<div class="scene-move">' +
        '<button type="button" class="icon-btn" data-move="-1" title="Flyt op"' + (i === 0 ? " disabled" : "") + ">▲</button>" +
        '<button type="button" class="icon-btn" data-move="1" title="Flyt ned"' + (i === S.scenes.length - 1 ? " disabled" : "") + ">▼</button></div>";
      ul.appendChild(li);
      var thumb = li.querySelector(".scene-thumb");
      SceneRender.mount(thumb, contentOf(sc), (S.state && S.state.canvas) || { width: 1080, height: 1920 }, { thumbs: true });
      li.querySelector(".scene-pick").onclick = function () { selectScene(sc.id, false); };
      li.querySelectorAll("[data-move]").forEach(function (b) {
        b.onclick = function () { moveScene(i, parseInt(b.dataset.move, 10)); };
      });
    });
  }

  function moveScene(i, d) {
    var ids = S.scenes.map(function (s) { return s.id; });
    var j = i + d;
    if (j < 0 || j >= ids.length) return;
    var t = ids[i]; ids[i] = ids[j]; ids[j] = t;
    Tal.run(null, function () { return Tal.api("PUT", "/api/scenes/order", { ids: ids }).then(function (r) { S.scenes = r.scenes; renderScenes(); }); });
  }

  // Klik på scene: indlæs i editor og (med kontrol) i preview. Live ændres ikke.
  function selectScene(id, silent, noPreview) {
    if (S.editorDirty && id !== S.selectedId && !confirm("Scenen har ændringer, der ikke er gemt. Kassér dem?")) return;
    S.selectedId = id;
    S.editorDirty = false;
    loadEditor(id ? sceneById(id) : null);
    renderScenes();
    renderQuickBase();
    if (!id || noPreview) return;
    if (!haveControl()) { if (!silent) Tal.toast("Scenen er indlæst i editoren. Overtag kontrol for at klargøre den.", "info"); return; }
    previewScene(id, null);
  }

  function previewScene(id, override) {
    return Tal.run(null, function () {
      return Tal.api("POST", "/api/scenes/" + id + "/preview", override || {}).then(function (r) { applyState(r.state); renderAll(); });
    });
  }

  $("btn-new-scene").onclick = function () {
    var name = prompt("Navn på den nye scene:", "Ny scene");
    if (!name) return;
    Tal.run(this, function () {
      return Tal.api("POST", "/api/scenes", { name: name, primary_text: name }).then(function (r) {
        S.scenes.push(r.scene); S.editorDirty = false; selectScene(r.scene.id, true, true);
      });
    });
  };

  // ------------------------------------------------------------ editor

  var form = $("editor");
  var FIELDS = ["name", "primary_text", "secondary_text", "show_primary", "show_secondary", "primary_color",
    "secondary_color", "primary_size", "secondary_size", "h_align", "v_align", "text_shadow", "bg_color",
    "bg_image_id", "bg_fit", "show_logo", "logo_image_id", "logo_position", "logo_size"];

  function fillImageSelects() {
    form.querySelectorAll("[data-image-select]").forEach(function (sel) {
      var val = sel.value;
      sel.innerHTML = '<option value="">Intet</option>' + S.uploads.map(function (u) {
        return '<option value="' + u.id + '">' + esc(u.original_name) + " (" + u.width + "×" + u.height + ")</option>";
      }).join("");
      sel.value = val;
    });
  }

  function loadEditor(scene) {
    form.hidden = !scene;
    $("editor-empty").hidden = !!scene;
    $("btn-duplicate").disabled = $("btn-delete").disabled = !scene;
    if (!scene) { S.draft = null; return; }
    fillImageSelects();
    FIELDS.forEach(function (f) {
      var inp = form.elements[f];
      if (!inp) return;
      var v = scene[f];
      if (inp.type === "checkbox") inp.checked = !!v;
      else inp.value = v == null ? "" : v;
    });
    S.draft = readEditor();
    setEditorDirty(false);
    renderDraft();
  }

  function readEditor() {
    var d = {};
    FIELDS.forEach(function (f) {
      var inp = form.elements[f];
      if (!inp) return;
      if (inp.type === "checkbox") d[f] = inp.checked;
      else if (inp.type === "number") d[f] = parseInt(inp.value, 10) || 0;
      else if (f === "bg_image_id" || f === "logo_image_id") d[f] = inp.value ? parseInt(inp.value, 10) : null;
      else d[f] = inp.value;
    });
    return d;
  }

  function setEditorDirty(on) { S.editorDirty = on; $("editor-dirty").hidden = !on; form.classList.toggle("dirty", on); }

  function renderDraft() {
    if (!S.draft) return;
    SceneRender.mount($("draft-stage"), contentOf(S.draft), (S.state && S.state.canvas) || { width: 1080, height: 1920 });
  }

  form.addEventListener("input", function () { S.draft = readEditor(); setEditorDirty(true); renderDraft(); });
  form.addEventListener("change", function () { S.draft = readEditor(); setEditorDirty(true); renderDraft(); });

  function saveScene(btn) {
    var id = S.selectedId;
    return Tal.api("PUT", "/api/scenes/" + id, readEditor()).then(function (r) {
      for (var i = 0; i < S.scenes.length; i++) if (S.scenes[i].id === id) S.scenes[i] = r.scene;
      setEditorDirty(false);
      renderScenes();
      return r;
    });
  }

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    Tal.run($("btn-save"), function () { return saveScene(); }, "Scenen er gemt. Live er uændret.");
  });
  $("btn-save-preview").onclick = function () {
    var btn = this;
    if (!haveControl()) { Tal.toast("Overtag kontrol for at klargøre.", "error"); return; }
    Tal.run(btn, function () {
      return saveScene().then(function () {
        return Tal.api("POST", "/api/scenes/" + S.selectedId + "/preview", {});
      }).then(function (r) { applyState(r.state); renderAll(); });
    }, "Gemt og klargjort.");
  };
  $("btn-revert").onclick = function () { setEditorDirty(false); loadEditor(sceneById(S.selectedId)); };
  $("btn-duplicate").onclick = function () {
    Tal.run(this, function () {
      return Tal.api("POST", "/api/scenes/" + S.selectedId + "/duplicate").then(function (r) {
        return loadScenes().then(function () { S.editorDirty = false; selectScene(r.scene.id, true, true); });
      });
    }, "Scenen er duplikeret.");
  };
  $("btn-delete").onclick = function () {
    var sc = sceneById(S.selectedId);
    if (!sc || !confirm("Slet scenen '" + sc.name + "'? Det kan ikke fortrydes.")) return;
    Tal.run(this, function () {
      return Tal.api("DELETE", "/api/scenes/" + sc.id).then(function () {
        S.editorDirty = false; S.selectedId = null; return loadScenes();
      });
    }, "Scenen er slettet.");
  };

  // ------------------------------------------------------------ VIS / fallback / frigiv

  $("btn-vis").onclick = function () {
    var p = S.state && S.state.preview;
    if (!p) return;
    Tal.run(this, function () {
      return Tal.api("POST", "/api/preview/activate", { preview_rev: p.rev }).then(function (r) {
        applyState(r.state); hideAlert(); renderAll();
      });
    });
  };

  $("btn-fallback").onclick = function () {
    if (!confirm("Vis fallback-scenen på talerstolen NU?")) return;
    Tal.run(this, function () {
      return Tal.api("POST", "/api/fallback/activate").then(function (r) { applyState(r.state); renderAll(); });
    }, "Fallback vises.");
  };

  $("btn-acquire").onclick = function () { acquire(this, false); };

  $("btn-release").onclick = function () {
    if (!confirm("Vil du frigive kontrollen? Den aktuelle scene bliver stående, og automatisk fallback ved tab af kontrolforbindelse bliver sat på pause.")) return;
    var btn = this;
    Tal.run(btn, function () {
      return Tal.api("POST", "/api/control/release").then(function (r) {
        setSession(null); applyState(r.state); renderAll();
      });
    }, "Kontrollen er frigivet. Scenen bliver stående.");
  };

  // ------------------------------------------------------------ hurtigtekster

  function loadQuick(fromBroadcast) {
    return Tal.api("GET", "/api/quicktexts").then(function (r) {
      if (fromBroadcast && Object.keys(S.quickDirty).length) {
        Tal.toast("Hurtigtekster er ændret fra en anden maskine. Dine ikke-gemte ændringer er bevaret.", "info");
        S.quickSaved = r.quick_texts; return;
      }
      S.quick = r.quick_texts; S.quickSaved = r.quick_texts; S.quickDirty = {};
      renderQuick();
    });
  }

  function renderQuickBase() {
    var sc = sceneById(S.selectedId);
    $("quick-base").textContent = sc ? sc.name : "–";
  }

  function renderQuick() {
    var ol = $("quick-list");
    ol.innerHTML = "";
    S.quick.forEach(function (q) {
      var li = document.createElement("li");
      li.className = "quick-item";
      li.dataset.id = q.id;
      li.innerHTML =
        '<span class="quick-slot">' + q.slot + "</span>" +
        '<input class="q-primary" maxlength="200" placeholder="Navn" value="' + esc(q.primary_text) + '">' +
        '<input class="q-secondary" maxlength="300" placeholder="Titel / organisation" value="' + esc(q.secondary_text) + '">' +
        '<button type="button" class="btn btn-sm" data-quick-preview>Klargør</button>';
      ol.appendChild(li);
      var p = li.querySelector(".q-primary"), s = li.querySelector(".q-secondary");
      function onInput() {
        var saved = savedQuick(q.id);
        var dirty = !saved || p.value !== saved.primary_text || s.value !== saved.secondary_text;
        if (dirty) S.quickDirty[q.id] = true; else delete S.quickDirty[q.id];
        li.classList.toggle("dirty", dirty);
        updateQuickDirty();
      }
      p.oninput = onInput; s.oninput = onInput;
      li.querySelector("[data-quick-preview]").onclick = function () {
        if (!S.selectedId) { Tal.toast("Vælg en scene som baggrund først.", "error"); return; }
        if (!haveControl()) { Tal.toast("Overtag kontrol for at klargøre.", "error"); return; }
        previewScene(S.selectedId, { quick_text_id: q.id, primary_text: p.value, secondary_text: s.value });
      };
    });
    updateQuickDirty();
  }

  function savedQuick(id) {
    var list = S.quickSaved || [];
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
    return null;
  }

  function updateQuickDirty() {
    var n = Object.keys(S.quickDirty).length;
    $("quick-dirty").hidden = !n;
    $("quick-dirty").textContent = n === 1 ? "1 ændring ikke gemt" : n + " ændringer ikke gemt";
    $("btn-quick-save").disabled = !n;
  }

  $("btn-quick-save").onclick = function () {
    var items = [];
    $("quick-list").querySelectorAll(".quick-item").forEach(function (li) {
      items.push({ id: parseInt(li.dataset.id, 10), primary_text: li.querySelector(".q-primary").value,
                   secondary_text: li.querySelector(".q-secondary").value });
    });
    Tal.run(this, function () {
      return Tal.api("PUT", "/api/quicktexts", { items: items }).then(function (r) {
        S.quick = r.quick_texts; S.quickSaved = r.quick_texts; S.quickDirty = {}; renderQuick();
      });
    }, "Hurtigtekster gemt.");
  };

  // ------------------------------------------------------------ billeder

  function loadUploads() {
    return Tal.api("GET", "/api/uploads").then(function (r) {
      S.uploads = r.uploads;
      renderUploads();
      fillImageSelects();
      renderScenes();
      renderDraft();
    });
  }

  function thumbHtml(u) {
    return '<figure class="thumb" data-id="' + u.id + '"><img src="' + esc(u.thumb_url) + '" alt="" loading="lazy">' +
      '<figcaption title="' + esc(u.original_name) + '">' + esc(u.original_name) + "</figcaption>" +
      '<div class="thumb-actions"><button type="button" class="btn btn-xs" data-use="bg_image_id">Baggrund</button>' +
      '<button type="button" class="btn btn-xs" data-use="logo_image_id">Logo</button>' +
      '<button type="button" class="btn btn-xs btn-ghost" data-del title="Slet">✕</button></div></figure>';
  }

  function bindThumbs(box) {
    box.querySelectorAll(".thumb").forEach(function (fig) {
      var id = parseInt(fig.dataset.id, 10);
      fig.querySelectorAll("[data-use]").forEach(function (b) {
        b.onclick = function () {
          if (form.hidden) { Tal.toast("Vælg en scene først.", "error"); return; }
          var field = b.dataset.use;
          form.elements[field].value = String(id);
          if (field === "logo_image_id") form.elements.show_logo.checked = true;
          S.draft = readEditor(); setEditorDirty(true); renderDraft();
          Tal.toast("Billedet er sat i kladden. Husk at gemme scenen.", "info");
          var dlg = $("images-dialog"); if (dlg.open) dlg.close();
        };
      });
      fig.querySelector("[data-del]").onclick = function () {
        if (!confirm("Slet billedet permanent?")) return;
        Tal.run(null, function () { return Tal.api("DELETE", "/api/uploads/" + id).then(loadUploads); }, "Billedet er slettet.");
      };
    });
  }

  function renderUploads() {
    var recent = $("recent-uploads");
    recent.innerHTML = S.uploads.slice(0, 5).map(thumbHtml).join("") || '<p class="muted small">Ingen billeder endnu.</p>';
    bindThumbs(recent);
    var all = $("all-uploads");
    all.innerHTML = S.uploads.map(thumbHtml).join("") || '<p class="muted">Ingen billeder endnu.</p>';
    bindThumbs(all);
  }

  $("upload-input").onchange = function () {
    var files = Array.prototype.slice.call(this.files || []);
    this.value = "";
    files.reduce(function (p, f) {
      return p.then(function () {
        var fd = new FormData(); fd.append("file", f);
        return Tal.api("POST", "/api/uploads", fd).then(function () {
          Tal.toast("Uploadet: " + f.name, "ok");
        }).catch(function (err) { Tal.toast(f.name + ": " + err.message, "error"); });
      });
    }, Promise.resolve()).then(loadUploads);
  };

  $("btn-all-images").onclick = function () { $("images-dialog").showModal(); };
  document.querySelectorAll("[data-close]").forEach(function (b) { b.onclick = function () { b.closest("dialog").close(); }; });

  // ------------------------------------------------------------ samlet render

  function renderAll() {
    renderControl();
    renderStages();
    renderScenes();
  }
  Tal.afterRun = function () { renderControl(); };

  window.addEventListener("beforeunload", function (ev) {
    if (S.editorDirty || Object.keys(S.quickDirty).length) { ev.preventDefault(); ev.returnValue = ""; }
  });

  // Opdatér "sekunder siden" mellem serverbeskeder.
  setInterval(renderControl, 1000);

  // ------------------------------------------------------------ start

  (function resume() {
    try {
      var saved = JSON.parse(sessionStorage.getItem(SESSION_KEY) || "null");
      if (saved && saved.id) setSession(saved.id, saved.at);
    } catch (e) {}
  })();

  Promise.all([loadUploads(), loadQuick()]).then(loadScenes).catch(function () {});
  Tal.socket("/ws/admin", onMessage);
})();
