// Немного удобства поверх обычных форм. Сайт работает и без JavaScript.
(function () {
  function bind(root) {
    // Кнопки «−» и «+» у полей дней/часов/минут.
    root.querySelectorAll("[data-step]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var input = document.getElementById(btn.dataset.target);
        var max = parseInt(input.max || "999", 10);
        var value = (parseInt(input.value || "0", 10) || 0) + parseInt(btn.dataset.step, 10);
        input.value = Math.max(0, Math.min(max, value));
      });
    });

    // Цикл раздачи: готовые варианты «1 : 2» и подпись, как будет идти раздача.
    root.querySelectorAll("[data-cycle]").forEach(function (box) {
      var big = box.querySelector("#cycle_big"), wait = box.querySelector("#cycle_wait");
      var first = box.querySelector("#cycle_first"), text = box.querySelector("[data-cycle-text]");
      var chips = box.querySelectorAll("[data-big]");
      function update() {
        var b = +big.value, w = +wait.value, wf = first.value === "wait";
        if (!b && !w) text.textContent = "нужно хотя бы 1 баф в цикле";
        else if (!w) text.textContent = "только большим таймерам";
        else if (!b) text.textContent = "только тем, кому досталось меньше всех";
        else if (wf) text.textContent = b + " : " + w + " — сначала " + w + " меньше получившим, потом " + b + " большим";
        else text.textContent = b + " : " + w + " — сначала " + b + " большим, потом " + w + " меньше получившим";
        chips.forEach(function (c) {
          c.setAttribute("aria-pressed", String(+c.dataset.big === b && +c.dataset.wait === w));
        });
      }
      chips.forEach(function (c) {
        c.addEventListener("click", function () {
          big.value = c.dataset.big; wait.value = c.dataset.wait; update();
        });
      });
      [big, wait, first].forEach(function (el) { el.addEventListener("change", update); });
      update();
    });

    // «Ещё совет» — подменяем карточку без перезагрузки страницы.
    root.querySelectorAll("[data-next-tip]").forEach(function (link) {
      link.addEventListener("click", function (e) {
        e.preventDefault();
        fetch("/tip?step=" + link.dataset.nextTip, { credentials: "same-origin" })
          .then(function (r) { if (!r.ok) throw new Error(); return r.text(); })
          .then(function (html) {
            var fresh = new DOMParser().parseFromString(html, "text/html").querySelector("[data-tip]");
            var box = link.closest("[data-tip]");
            if (fresh && box) { box.replaceWith(fresh); bind(fresh); }
          })
          .catch(function () { location.href = link.href; });
      });
    });

    // Подтверждение важных действий.
    root.querySelectorAll("form[data-confirm]").forEach(function (form) {
      form.addEventListener("submit", function (e) {
        if (!window.confirm(form.dataset.confirm)) e.preventDefault();
      });
    });

    // Скопировать ник, чтобы вставить в поиск в игре.
    root.querySelectorAll("[data-copy]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var done = function () {
          var old = btn.textContent;
          btn.textContent = "✅ Скопировано";
          setTimeout(function () { btn.textContent = old; }, 1500);
        };
        if (navigator.clipboard) navigator.clipboard.writeText(btn.dataset.copy).then(done, function () {});
      });
    });

    // Поиск по игрокам в управлении.
    var search = root.querySelector("#player-search");
    if (search) {
      search.addEventListener("input", function () {
        var q = search.value.trim().toLowerCase();
        document.querySelectorAll("[data-nick], .journal li").forEach(function (row) {
          var text = (row.dataset.nick || row.textContent).toLowerCase();
          row.hidden = q && text.indexOf(q) === -1;
        });
      });
    }

  }

  // Живые данные: блок с атрибутом data-live каждые 10 секунд подтягивает свежую версию
  // с сервера. Раскрытые списки остаются раскрытыми. Пока вкладка скрыта — пауза.
  var LIVE_MS = 10000;
  var loading = false;

  function refresh() {
    var box = document.querySelector("[data-live]");
    if (!box || loading || document.hidden) return;
    loading = true;
    var open = {};
    box.querySelectorAll("details[open][data-key]").forEach(function (d) { open[d.dataset.key] = true; });
    fetch(box.dataset.live, { credentials: "same-origin", cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("skip"); return r.text(); })
      .then(function (html) {
        var fresh = new DOMParser().parseFromString(html, "text/html").querySelector("[data-live]");
        if (!fresh) return;
        fresh.querySelectorAll("details[data-key]").forEach(function (d) { if (open[d.dataset.key]) d.open = true; });
        fresh.classList.add("no-anim");
        box.replaceWith(fresh);
        bind(fresh);
      })
      .catch(function () {})
      .then(function () { loading = false; });
  }

  // Уведомления на телефон (Web Push): включить/выключить на этом устройстве.
  function initPush() {
    var boxes = document.querySelectorAll("[data-push]");
    var key = document.body.dataset.vapid, csrf = document.body.dataset.csrf;
    if (!boxes.length || !csrf) return;
    var ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
    var standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
    var supported = !!key && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
    function all(sel, fn) { document.querySelectorAll(sel).forEach(fn); }
    function show(sel, on) { all(sel, function (el) { el.hidden = !on; }); }
    function status(text) { all("[data-push-status]", function (el) { el.textContent = text; }); }
    function post(url, body) {
      body.csrf = csrf;
      return fetch(url, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    }
    function bytes(b64) {
      var pad = "=".repeat((4 - b64.length % 4) % 4);
      var raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
      var out = new Uint8Array(raw.length);
      for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
      return out;
    }
    function state(on) {
      show("[data-push-on]", !on); show("[data-push-off]", on); show("[data-push-banner]", !on);
      if (on) status("✅ Включено на этом устройстве.");
    }
    if (!supported) {
      if (ios && !standalone) {
        show("[data-push-ios]", true); show("[data-push-banner]", true);
        all("[data-push-banner] [data-push-on]", function (b) { b.addEventListener("click", function () { location.href = "/me#notify"; }); });
        all("#notify [data-push-on]", function (b) { b.hidden = true; });
      } else {
        status("Этот браузер не умеет уведомления. На Android открой сайт в Chrome, на iPhone — добавь на экран «Домой».");
        show("[data-push-on]", false);
      }
      return;
    }
    var regPromise = navigator.serviceWorker.register("/sw.js");
    regPromise.then(function (reg) { return reg.pushManager.getSubscription(); }).then(function (sub) {
      if (sub) { post("/push/subscribe", sub.toJSON()); state(true); }
      else { state(false); if (Notification.permission === "denied") show("[data-push-denied]", true); }
    }).catch(function () {});

    all("[data-push-on]", function (btn) {
      btn.addEventListener("click", function () {
        btn.disabled = true;
        Notification.requestPermission().then(function (perm) {
          if (perm !== "granted") { show("[data-push-denied]", true); throw new Error("denied"); }
          return regPromise;
        }).then(function (reg) {
          return reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: bytes(key) });
        }).then(function (sub) {
          return post("/push/subscribe", sub.toJSON());
        }).then(function (r) {
          if (!r.ok) throw new Error("server");
          state(true); show("[data-push-denied]", false);
        }).catch(function (e) {
          if (e.message !== "denied") status("Не получилось включить. Обнови страницу и попробуй ещё раз.");
        }).then(function () { btn.disabled = false; });
      });
    });
    all("[data-push-off]", function (btn) {
      btn.addEventListener("click", function () {
        regPromise.then(function (reg) { return reg.pushManager.getSubscription(); }).then(function (sub) {
          if (!sub) return;
          return post("/push/unsubscribe", { endpoint: sub.endpoint }).then(function () { return sub.unsubscribe(); });
        }).then(function () { state(false); status("Выключено на этом устройстве."); });
      });
    });
  }

  // Часы в шапке: местное время (МСК) и серверное (UTC), идут каждую секунду.
  // Отсчёт — от времени сервера сайта, а не от часов телефона (они бывают сбиты).
  function initClock() {
    var box = document.querySelector("[data-clock]");
    if (!box) return;
    var skew = parseInt(box.dataset.now, 10) * 1000 - Date.now();
    var off = parseInt(box.dataset.off, 10) * 60000;
    var loc = box.querySelector("[data-clock-local]"), utc = box.querySelector("[data-clock-utc]");
    function fmt(ms) { return new Date(ms).toISOString().substr(11, 8); }
    function tick() {
      var t = Date.now() + skew;
      utc.textContent = fmt(t);
      loc.textContent = fmt(t + off);
    }
    tick();
    setInterval(tick, 1000);
  }

  initClock();
  initPush();
  bind(document);
  if (document.querySelector("[data-live]")) {
    setInterval(refresh, LIVE_MS);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) refresh(); });
  }
})();
