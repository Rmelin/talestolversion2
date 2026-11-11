(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var esc = Tal.esc, form = $("settings-form");

  function load() {
    return Promise.all([Tal.api("GET", "/api/settings"), Tal.api("GET", "/api/scenes")]).then(function (r) {
      var st = r[0], scenes = r[1].scenes;
      form.elements.fallback_scene_id.innerHTML = '<option value="">(Ingen – sort skærm)</option>' +
        scenes.map(function (s) { return '<option value="' + s.id + '">' + esc(s.name) + "</option>"; }).join("");
      ["fallback_scene_id", "heartbeat_interval", "warning_after", "fallback_after", "screen_format",
       "default_primary_size", "default_secondary_size"].forEach(function (k) {
        form.elements[k].value = st[k] == null ? "" : st[k];
      });
      form.elements.auto_fallback.checked = st.auto_fallback;
    });
  }

  form.onsubmit = function (ev) {
    ev.preventDefault();
    var e = form.elements;
    var body = {
      fallback_scene_id: e.fallback_scene_id.value ? parseInt(e.fallback_scene_id.value, 10) : null,
      auto_fallback: e.auto_fallback.checked,
      heartbeat_interval: parseInt(e.heartbeat_interval.value, 10),
      warning_after: parseInt(e.warning_after.value, 10),
      fallback_after: parseInt(e.fallback_after.value, 10),
      screen_format: e.screen_format.value,
      default_primary_size: parseInt(e.default_primary_size.value, 10),
      default_secondary_size: parseInt(e.default_secondary_size.value, 10)
    };
    if (!body.auto_fallback && !confirm("Uden automatisk fallback bliver scenen stående, selv hvis kontrolmaskinen forsvinder. Fortsæt?")) return;
    Tal.run(form.querySelector("button[type=submit]"), function () {
      return Tal.api("PUT", "/api/settings", body).then(function () { loadLog(); });
    }, "Indstillingerne er gemt.");
  };

  function loadSys() {
    return Tal.api("GET", "/api/system/status").then(function (s) {
      var c = s.control;
      $("status").innerHTML = Tal.stateBadge(c);
      var rows = [
        ["Driftstilstand", esc(c.label)],
        ["Kontrolsession", c.holder ? esc(c.holder.label) : "Ingen"],
        ["Seneste heartbeat", Tal.fmtClock(c.last_heartbeat_at)],
        ["Live-scene", s.live ? esc(s.live.name) + (s.live.is_fallback ? " (fallback)" : "") : "–"],
        ["Forbindelser", "Display " + s.connections.display + " · Kontrol " + s.connections.admin + " · Monitor " + s.connections.monitor],
        ["Database", esc(s.database)],
        ["Ledig disk", s.disk_free_mb + " MB"],
        ["Scener / billeder", s.scenes + " / " + s.uploads],
        ["Startet", Tal.fmtDateTime(s.started_at) + " (oppe " + Tal.fmtSecs(s.uptime_seconds) + ")"],
        ["Version", esc(s.version)]
      ];
      s.warnings.forEach(function (w) { rows.push(["Advarsel", '<span class="warn-text">' + esc(w) + "</span>"]); });
      $("sys").innerHTML = rows.map(function (r) { return "<dt>" + r[0] + "</dt><dd>" + r[1] + "</dd>"; }).join("");
    }).catch(function () {});
  }

  function loadLog() {
    return Tal.api("GET", "/api/events?limit=300").then(function (r) {
      $("log").innerHTML = r.events.map(function (e) {
        return '<tr class="lvl-' + esc(e.level) + '"><td>' + Tal.fmtDateTime(e.at) + "</td><td>" + esc(e.kind) +
          "</td><td>" + esc(e.message) + "</td></tr>";
      }).join("");
    });
  }
  $("btn-log").onclick = loadLog;

  load(); loadSys(); loadLog();
  setInterval(loadSys, 3000);
})();
