/* Fælles klientlogik: API-kald, WebSocket, toasts og formatering.
 * Serveren er autoritativ – klienten viser kun det, serveren har bekræftet. */
(function () {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function fmtClock(iso) {
    if (!iso) return "–";
    var d = new Date(iso);
    if (isNaN(d)) return "–";
    return pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }
  function fmtDateTime(iso) {
    if (!iso) return "–";
    var d = new Date(iso);
    return pad(d.getDate()) + "/" + pad(d.getMonth() + 1) + " " + fmtClock(iso);
  }
  function fmtSecs(s) {
    if (s == null) return "–";
    s = Math.max(0, Math.round(s));
    if (s < 60) return s + " s";
    return Math.floor(s / 60) + ":" + pad(s % 60) + " min";
  }

  var STATE = {
    CONTROL_ACTIVE:   { short: "Aktiv kontrol",        cls: "ok" },
    CONTROL_WARNING:  { short: "Forbindelsesadvarsel", cls: "warn" },
    CONTROL_RELEASED: { short: "Kontrol frigivet",     cls: "info" },
    FALLBACK_ACTIVE:  { short: "Fallback aktiv",       cls: "bad" }
  };

  // ------------------------------------------------------------ toast

  function toast(msg, kind) {
    var box = document.getElementById("toasts");
    if (!box) { alert(msg); return; }
    var el = document.createElement("div");
    el.className = "toast toast-" + (kind || "info");
    el.setAttribute("role", kind === "error" ? "alert" : "status");
    el.innerHTML = "<span>" + esc(msg) + '</span><button type="button" class="toast-x" aria-label="Luk">×</button>';
    el.querySelector("button").onclick = function () { el.remove(); };
    box.appendChild(el);
    setTimeout(function () { el.remove(); }, kind === "error" ? 9000 : 3500);
  }

  // ------------------------------------------------------------ API

  function ApiError(message, status, code) { this.message = message; this.status = status; this.code = code; }

  var extraHeaders = {};

  function api(method, url, body, opts) {
    opts = opts || {};
    var headers = { "X-Talerstol-Client": "1", "Accept": "application/json" };
    for (var k in extraHeaders) if (extraHeaders[k]) headers[k] = extraHeaders[k];
    var init = { method: method, credentials: "same-origin", headers: headers };
    if (body instanceof FormData) init.body = body;
    else if (body !== undefined) { headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
    return fetch(url, init).catch(function () {
      throw new ApiError("Kunne ikke kontakte talerstolen. Handlingen er IKKE gennemført.", 0);
    }).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (data) {
        if (res.ok) return data;
        if (res.status === 401 && !opts.noRedirect) {
          location.href = "/login?next=" + encodeURIComponent(location.pathname);
        }
        var msg = "Handlingen mislykkedes (" + res.status + ").";
        if (data && typeof data.detail === "string") msg = data.detail;
        else if (data && Array.isArray(data.detail)) {
          msg = "Ugyldige data: " + data.detail.map(function (d) {
            return (d.loc ? d.loc[d.loc.length - 1] + " – " : "") + d.msg;
          }).join("; ");
        }
        throw new ApiError(msg, res.status, data && data.code);
      });
    });
  }

  // Kør en handling fra en knap: lås knappen og vis fejl.
  function run(btn, fn, okMsg) {
    if (btn && btn.dataset.busy === "1") return Promise.resolve();
    if (btn) { btn.dataset.busy = "1"; btn.classList.add("busy"); btn.disabled = true; }
    return Promise.resolve().then(fn).then(function (res) {
      if (okMsg) toast(okMsg, "ok");
      return res;
    }).catch(function (err) {
      toast((err && err.message) || "Der opstod en fejl.", "error");
      return Promise.reject(err);
    }).finally(function () {
      if (btn) { btn.dataset.busy = ""; btn.classList.remove("busy"); btn.disabled = false; }
      if (Tal.afterRun) Tal.afterRun();
    }).catch(function () {});
  }

  // ------------------------------------------------------------ WebSocket

  function socket(path, onMessage, onStatus) {
    var ws = null, retries = 0, lastMsg = 0, connected = false;
    function setStatus(ok) {
      if (ok === connected) return;
      connected = ok;
      document.body.classList.toggle("is-offline", !ok);
      var banner = document.getElementById("conn-banner");
      if (banner) banner.hidden = ok;
      if (onStatus) onStatus(ok);
    }
    function connect() {
      var proto = location.protocol === "https:" ? "wss://" : "ws://";
      try { ws = new WebSocket(proto + location.host + path); } catch (e) { retry(); return; }
      var sock = ws;
      sock.onopen = function () { lastMsg = Date.now(); retries = 0; };
      sock.onmessage = function (ev) {
        lastMsg = Date.now();
        var msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
        setStatus(true);
        onMessage(msg);
      };
      sock.onclose = function (ev) {
        if (sock !== ws) return;
        setStatus(false);
        if (ev.code === 4401) { location.href = "/login?next=" + encodeURIComponent(location.pathname); return; }
        retry();
      };
      sock.onerror = function () { try { sock.close(); } catch (e) {} };
    }
    function retry() { retries++; setTimeout(connect, Math.min(5000, 400 * retries)); }
    setInterval(function () {
      if (ws && ws.readyState === 1 && Date.now() - lastMsg > 8000) { try { ws.close(); } catch (e) {} }
    }, 2000);
    connect();
    return { isConnected: function () { return connected; } };
  }

  var Tal = {
    esc: esc, fmtClock: fmtClock, fmtDateTime: fmtDateTime, fmtSecs: fmtSecs, STATE: STATE,
    toast: toast, api: api, run: run, socket: socket, headers: extraHeaders, afterRun: null,
    stateBadge: function (control) {
      var s = STATE[control.state] || { short: control.state, cls: "info" };
      return '<span class="state-pill state-' + s.cls + '"><span class="dot"></span>' + esc(s.short) + "</span>";
    }
  };
  window.Tal = Tal;
})();
