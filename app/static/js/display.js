/* Talerstolens display. Ingen knapper, ingen synlig genindlæsning.
 * Ny scene bygges i et skjult lag, alle billeder hentes og afkodes, og først
 * derefter skiftes hele laget på én gang. Ved afbrydelse bevares det senest
 * viste indhold, og forbindelsen genoprettes automatisk. */
(function () {
  "use strict";

  var stage = document.getElementById("stage");
  var current = null;          // aktivt lag
  var shownKey = null;         // boot_id:rev for det viste
  var pendingToken = 0;
  var canvas = { width: 1080, height: 1920 };

  function layerFor(content) {
    var layer = document.createElement("div");
    layer.className = "display-layer";
    var scene = SceneRender.build(content, canvas);
    layer.appendChild(scene);
    layer._scene = scene;
    return layer;
  }

  function fitAll() {
    var layers = stage.querySelectorAll(".display-layer");
    for (var i = 0; i < layers.length; i++) SceneRender.fit(stage, layers[i]._scene, canvas);
  }
  window.addEventListener("resize", fitAll);

  function show(snap) {
    if (!snap || !snap.live) return;
    var key = snap.boot_id + ":" + snap.live.rev;
    var canvasChanged = snap.canvas && (snap.canvas.width !== canvas.width || snap.canvas.height !== canvas.height);
    if (key === shownKey && !canvasChanged) return;
    if (snap.canvas) canvas = snap.canvas;
    var token = ++pendingToken;
    var layer = layerFor(snap.live.content);
    layer.classList.add("incoming");
    stage.appendChild(layer);
    SceneRender.fit(stage, layer._scene, canvas);
    // Vent på billeder, så navn og baggrund altid skifter samlet.
    SceneRender.ready(layer._scene, 8000).then(function () {
      if (token !== pendingToken) { layer.remove(); return; } // en nyere scene er på vej
      requestAnimationFrame(function () {
        layer.classList.remove("incoming");
        var old = current;
        current = layer;
        shownKey = key;
        if (old) setTimeout(function () { old.remove(); }, 400);
      });
    });
  }

  // ------------------------------------------------------------ WebSocket med automatisk genopretning
  var retries = 0, lastMsg = 0, ws = null;

  function connect() {
    var proto = location.protocol === "https:" ? "wss://" : "ws://";
    try { ws = new WebSocket(proto + location.host + "/ws/display"); }
    catch (e) { retry(); return; }
    var sock = ws;
    sock.onopen = function () { lastMsg = Date.now(); retries = 0; };
    sock.onmessage = function (ev) {
      lastMsg = Date.now();
      var msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (msg.type === "live") show(msg);
      document.body.classList.remove("offline");
    };
    sock.onclose = function () { if (sock === ws) { document.body.classList.add("offline"); retry(); } };
    sock.onerror = function () { try { sock.close(); } catch (e) {} };
  }

  function retry() {
    retries++;
    setTimeout(connect, Math.min(5000, 500 * retries));
  }

  // Server sender ping hvert 10. s. Høres intet i 25 s, er forbindelsen død.
  setInterval(function () {
    if (ws && ws.readyState === 1 && Date.now() - lastMsg > 25000) { try { ws.close(); } catch (e) {} }
  }, 5000);

  // Hent også tilstand via HTTP ved start (hurtigt første billede).
  fetch("/api/state", { credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : null; })
    .then(function (s) { if (s && s.live) show({ boot_id: s.boot_id, live: s.live, canvas: s.canvas }); })
    .catch(function () {});
  connect();

  // Undgå at skærmen går i dvale, hvis browseren understøtter det.
  function wake() {
    if (navigator.wakeLock && navigator.wakeLock.request) navigator.wakeLock.request("screen").catch(function () {});
  }
  document.addEventListener("visibilitychange", function () { if (!document.hidden) wake(); });
  wake();
})();
