/* Read-only monitor: live, næste scene og forbindelsesstatus. */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var esc = Tal.esc, state = null, lastLive = null, lastPrev = null, receivedAt = 0;

  function render() {
    if (!state) return;
    var c = state.control, h = c.holder;
    $("status").innerHTML = Tal.stateBadge(c) + '<span class="status-meta">' + esc(c.label) + "</span>";
    var since = c.seconds_since_heartbeat != null ? c.seconds_since_heartbeat + (Date.now() - receivedAt) / 1000 : null;
    var rows = [
      ["Driftstilstand", esc(c.label)],
      ["Kontrolsession", h ? esc(h.label) + " (siden " + Tal.fmtClock(h.acquired_at) + ")" : "Ingen"],
      ["Seneste heartbeat", Tal.fmtClock(c.last_heartbeat_at) + (since != null ? " · " + Tal.fmtSecs(since) + " siden" : "")],
      ["Fallback aktiv", state.live && state.live.is_fallback ? '<span class="tag tag-bad">JA</span>' : "Nej"],
      ["Automatisk fallback", c.timing.auto_fallback ? "Til – efter " + c.timing.fallback_after + " s" : '<span class="warn-text">FRA</span>'],
      ["Advarsel efter", c.timing.warning_after + " s"],
      ["Displays forbundet", String(state.displays || 0)],
      ["Seneste hændelse", Tal.fmtClock(c.reason_at) + " – " + esc(c.reason)]
    ];
    $("facts").innerHTML = rows.map(function (r) { return "<dt>" + r[0] + "</dt><dd>" + r[1] + "</dd>"; }).join("");
    var alert = $("mon-alert");
    var warn = c.state === "CONTROL_WARNING" || c.state === "FALLBACK_ACTIVE" || !state.displays;
    alert.hidden = !warn;
    alert.textContent = !state.displays ? "Advarsel: intet display er forbundet." : c.reason;

    var canvas = state.canvas;
    var lk = state.boot_id + ":" + (state.live ? state.live.rev : 0);
    if (lk !== lastLive && state.live) {
      lastLive = lk;
      SceneRender.mount($("live-stage"), state.live.content, canvas);
      $("live-name").innerHTML = esc(state.live.name) + " · " + Tal.fmtClock(state.live.at);
    }
    var pk = state.boot_id + ":" + (state.preview ? state.preview.rev : 0);
    if (pk !== lastPrev) {
      lastPrev = pk;
      if (state.preview) { SceneRender.mount($("preview-stage"), state.preview.content, canvas); $("preview-name").textContent = state.preview.name; }
      else { $("preview-stage").innerHTML = '<div class="empty">Ingen</div>'; $("preview-name").textContent = ""; }
    }
  }

  Tal.socket("/ws/monitor", function (msg) {
    if (msg.type === "state") { state = msg; receivedAt = Date.now(); }
    else if (msg.type === "control" && state) { state.control = msg.control; state.displays = msg.displays; receivedAt = Date.now(); }
    render();
  });
  setInterval(render, 1000);
})();
