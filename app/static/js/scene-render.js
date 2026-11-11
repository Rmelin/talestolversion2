/* Fælles scenerenderer – bruges af display, kontrolpanel, thumbnails og monitor.
 * En scene tegnes på et referencelærred (fx 1080×1920) og skaleres til sin
 * container med CSS transform, så alle visninger ser ens ud. */
(function () {
  "use strict";

  function el(tag, cls) { var e = document.createElement(tag); if (cls) e.className = cls; return e; }

  var JUSTIFY = { top: "flex-start", center: "center", bottom: "flex-end" };
  var ALIGN = { left: "flex-start", center: "center", right: "flex-end" };

  function imgUrl(img, thumbs) { return img ? (thumbs ? img.thumb_url : img.url) : null; }

  // Byg scenens DOM. Returnerer elementet (ikke indsat).
  function build(content, canvas, opts) {
    opts = opts || {};
    var c = content || {};
    var W = canvas.width, H = canvas.height;
    var scene = el("div", "scene");
    scene.style.width = W + "px";
    scene.style.height = H + "px";
    scene.style.background = c.bg_color || "#000";

    var bg = imgUrl(c.bg_image, opts.thumbs);
    if (bg) {
      var bgi = el("img", "scene-bg");
      bgi.src = bg; bgi.alt = ""; bgi.draggable = false;
      bgi.style.objectFit = c.bg_fit === "contain" ? "contain" : "cover";
      scene.appendChild(bgi);
    }

    var logo = imgUrl(c.logo_image, opts.thumbs);
    if (logo && c.show_logo) {
      var lz = el("div", "scene-zone");
      lz.style.justifyContent = JUSTIFY[c.logo_position] || "center";
      lz.style.alignItems = "center";
      var li = el("img", "scene-logo");
      li.src = logo; li.alt = ""; li.draggable = false;
      li.style.width = Math.round(W * (c.logo_size || 40) / 100) + "px";
      lz.appendChild(li);
      scene.appendChild(lz);
    }

    var showP = c.show_primary && c.primary_text;
    var showS = c.show_secondary && c.secondary_text;
    if (showP || showS) {
      var tz = el("div", "scene-zone scene-text");
      tz.style.justifyContent = JUSTIFY[c.v_align] || "flex-end";
      tz.style.alignItems = ALIGN[c.h_align] || "center";
      tz.style.textAlign = c.h_align || "center";
      if (c.text_shadow) tz.classList.add("shadow");
      if (showP) {
        var p = el("div", "scene-primary");
        p.textContent = c.primary_text;
        p.style.color = c.primary_color || "#fff";
        p.style.fontSize = (c.primary_size || 100) + "px";
        tz.appendChild(p);
      }
      if (showS) {
        var s = el("div", "scene-secondary");
        s.textContent = c.secondary_text;
        s.style.color = c.secondary_color || "#ddd";
        s.style.fontSize = (c.secondary_size || 60) + "px";
        tz.appendChild(s);
      }
      scene.appendChild(tz);
    }
    return scene;
  }

  // Skalér scenen ind i host (bevarer formatet, centreret).
  function fit(host, scene, canvas) {
    var w = host.clientWidth, h = host.clientHeight;
    if (!w || !h) return;
    var k = Math.min(w / canvas.width, h / canvas.height);
    var x = (w - canvas.width * k) / 2, y = (h - canvas.height * k) / 2;
    scene.style.transform = "translate(" + x + "px," + y + "px) scale(" + k + ")";
  }

  // Vent på at alle billeder i scenen er hentet og afkodet (maks. timeoutMs).
  function ready(scene, timeoutMs) {
    var imgs = scene.querySelectorAll("img");
    var jobs = [];
    for (var i = 0; i < imgs.length; i++) {
      (function (img) {
        jobs.push(new Promise(function (resolve) {
          if (img.decode) img.decode().then(resolve, resolve);
          else if (img.complete) resolve();
          else { img.onload = resolve; img.onerror = resolve; }
        }));
      })(imgs[i]);
    }
    var all = Promise.all(jobs);
    if (!timeoutMs) return all;
    return Promise.race([all, new Promise(function (r) { setTimeout(r, timeoutMs); })]);
  }

  // Montér en scene i en host og hold den skaleret. Erstatter tidligere indhold.
  function mount(host, content, canvas, opts) {
    host.classList.add("scene-host");
    var scene = build(content, canvas, opts);
    host.innerHTML = "";
    host.appendChild(scene);
    fit(host, scene, canvas);
    host._scene = scene; host._canvas = canvas;
    if (!host._ro && window.ResizeObserver) {
      host._ro = new ResizeObserver(function () { if (host._scene) fit(host, host._scene, host._canvas); });
      host._ro.observe(host);
    }
    return scene;
  }

  window.SceneRender = { build: build, fit: fit, ready: ready, mount: mount };
})();
