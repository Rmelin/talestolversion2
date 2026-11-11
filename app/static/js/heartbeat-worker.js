/* Heartbeat i en Web Worker: timere i workers neddrosles ikke som i en
 * baggrundsfane, så heartbeat bliver ved, selvom vinduet ikke er i fokus.
 * Et heartbeat tæller kun, når serveren har svaret OK – en åben fane uden
 * forbindelse til talerstolen sender altså ikke "falske" livstegn. */
"use strict";

var sessionId = null, interval = 10000, timer = null, inflight = false;

function beat() {
  if (!sessionId || inflight) return;
  inflight = true;
  var ctrl = new AbortController();
  var to = setTimeout(function () { ctrl.abort(); }, Math.max(3000, interval - 1000));
  fetch("/api/control/heartbeat", {
    method: "POST", credentials: "same-origin", signal: ctrl.signal,
    headers: { "Content-Type": "application/json", "X-Talerstol-Client": "1", "X-Control-Session": sessionId },
    body: JSON.stringify({ session_id: sessionId })
  }).then(function (res) {
    return res.json().catch(function () { return null; }).then(function (data) {
      postMessage({ type: res.ok ? "ok" : "rejected", status: res.status, data: data, session: sessionId });
    });
  }).catch(function () {
    postMessage({ type: "network", session: sessionId });
  }).finally(function () { clearTimeout(to); inflight = false; });
}

function schedule() {
  clearInterval(timer);
  if (sessionId) timer = setInterval(beat, interval);
}

onmessage = function (ev) {
  var m = ev.data || {};
  if (m.cmd === "start") {
    sessionId = m.sessionId; interval = Math.max(1000, (m.interval || 10) * 1000);
    schedule(); beat();
  } else if (m.cmd === "interval") {
    var next = Math.max(1000, (m.interval || 10) * 1000);
    if (next !== interval) { interval = next; schedule(); }
  } else if (m.cmd === "beat") {
    beat();
  } else if (m.cmd === "stop") {
    sessionId = null; clearInterval(timer);
  }
};
