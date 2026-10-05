// Немного удобства поверх обычных форм. Сайт работает и без JavaScript.
(function () {
  // Перевод: base.html кладёт в window.I18N {русский текст: перевод}; {имя} заменяется из vars.
  function T(key, vars) {
    var text = (window.I18N && window.I18N[key]) || key;
    return text.replace(/\{(\w+)\}/g, function (m, name) { return vars && name in vars ? vars[name] : m; });
  }

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
        if (!b && !w) text.textContent = T("нужно хотя бы 1 баф в цикле");
        else if (!w) text.textContent = T("только большим таймерам");
        else if (!b) text.textContent = T("только тем, кому досталось меньше всех");
        else if (wf) text.textContent = T("{big} : {wait} — сначала {wait} меньше получившим, потом {big} большим", {big: b, wait: w});
        else text.textContent = T("{big} : {wait} — сначала {big} большим, потом {wait} меньше получившим", {big: b, wait: w});
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
        if (!window.confirm(form.dataset.confirm)) { e.preventDefault(); e.stopImmediatePropagation(); }
      });
    });

    // Кнопка отправки: «отправляется…» и защита от двойного нажатия.
    root.querySelectorAll("form").forEach(function (form) {
      if (form.dataset.busyBound) return;
      form.dataset.busyBound = "1";
      form.addEventListener("submit", function (e) {
        if (e.defaultPrevented || form.method.toLowerCase() !== "post") return;
        var btn = e.submitter || form.querySelector("button[type=submit], button:not([type])");
        if (!btn) return;
        if (form.dataset.busy) { e.preventDefault(); return; }
        form.dataset.busy = "1";
        btn.classList.add("is-busy");
        btn.setAttribute("aria-busy", "true");
        setTimeout(function () { delete form.dataset.busy; btn.classList.remove("is-busy"); btn.removeAttribute("aria-busy"); }, 8000);
      });
    });

    // Таблицы, которые шире экрана: тень у края, пока есть что прокрутить.
    root.querySelectorAll(".table-wrap").forEach(function (box) {
      function upd() {
        box.classList.toggle("more-right", box.scrollLeft + box.clientWidth < box.scrollWidth - 2);
        box.classList.toggle("more-left", box.scrollLeft > 2);
      }
      box.addEventListener("scroll", upd, { passive: true });
      upd();
    });

    // Скопировать ник, чтобы вставить в поиск в игре.
    root.querySelectorAll("[data-copy]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var done = function () {
          var old = btn.textContent;
          btn.textContent = T("✅ Скопировано");
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
    // Не перерисовывать, пока человек что-то печатает внутри (например, ответ в Штабе R4).
    var typing = Array.prototype.some.call(box.querySelectorAll("input[type=text], textarea"), function (f) { return f.value; });
    if (typing || box.contains(document.activeElement) && /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) return;
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
      if (on) status(T("✅ Включено на этом устройстве."));
    }
    if (!supported) {
      if (ios && !standalone) {
        show("[data-push-ios]", true); show("[data-push-banner]", true);
        all("[data-push-banner] [data-push-on]", function (b) { b.addEventListener("click", function () { location.href = "/me#notify"; }); });
        all("#notify [data-push-on]", function (b) { b.hidden = true; });
      } else {
        status(T("Этот браузер не умеет уведомления. На Android открой сайт в Chrome, на iPhone — добавь на экран «Домой»."));
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
          if (e.message !== "denied") status(T("Не получилось включить. Обнови страницу и попробуй ещё раз."));
        }).then(function () { btn.disabled = false; });
      });
    });
    all("[data-push-off]", function (btn) {
      btn.addEventListener("click", function () {
        regPromise.then(function (reg) { return reg.pushManager.getSubscription(); }).then(function (sub) {
          if (!sub) return;
          return post("/push/unsubscribe", { endpoint: sub.endpoint }).then(function () { return sub.unsubscribe(); });
        }).then(function () { state(false); status(T("Выключено на этом устройстве.")); });
      });
    });
  }

  // Часы в шапке: местное время (МСК) и серверное (UTC), идут каждую секунду.
  // Отсчёт — от времени сервера сайта, а не от часов телефона (они бывают сбиты).
  function initClock() {
    document.querySelectorAll("[data-clock]").forEach(function (box) {
      var skew = parseInt(box.dataset.now, 10) * 1000 - Date.now();
      var off = parseInt(box.dataset.off, 10) * 60000;
      var loc = box.querySelector("[data-clock-local]"), utc = box.querySelector("[data-clock-utc]");
      var len = loc && loc.textContent.trim().length === 5 ? 5 : 8;  // ЧЧ:ММ или ЧЧ:ММ:СС
      function fmt(ms) { return new Date(ms).toISOString().substr(11, len); }
      function tick() {
        var t = Date.now() + skew;
        if (utc) utc.textContent = fmt(t);
        if (loc) loc.textContent = fmt(t + off);
      }
      tick();
      setInterval(tick, 1000);
    });
  }

  // Тема: тёмная/светлая, выбор запоминается в браузере.
  // Поле-фильтр списка: data-filter=".selector" прячет строки, где нет введённого текста.
  document.addEventListener("input", function (e) {
    var sel = e.target.dataset && e.target.dataset.filter;
    if (!sel) return;
    var q = e.target.value.trim().toLowerCase();
    var box = e.target.closest("fieldset, section, form") || document;
    box.querySelectorAll(sel).forEach(function (el) { el.hidden = q && el.textContent.toLowerCase().indexOf(q) < 0; });
  });

  // Ссылка на ответ (/faq#slug) сразу раскрывает его.
  function openHash() {
    var el = location.hash && document.getElementById(location.hash.slice(1));
    if (el && el.tagName === "DETAILS") { el.open = true; el.scrollIntoView({ block: "start" }); }
  }
  window.addEventListener("hashchange", openHash);
  document.addEventListener("DOMContentLoaded", openHash);

  // Всплывающее сообщение после действия: само исчезает через 6 секунд.
  document.querySelectorAll("[data-toast]").forEach(function (t) {
    function hide() { t.classList.add("toast-out"); setTimeout(function () { t.remove(); }, 250); }
    var timer = setTimeout(hide, 6000);
    t.addEventListener("mouseenter", function () { clearTimeout(timer); });
    var x = t.querySelector("[data-toast-close]");
    if (x) x.addEventListener("click", hide);
  });

  function initTheme() {
    document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) { btn.addEventListener("click", function () {
      var root = document.documentElement;
      var dark = root.dataset.theme ? root.dataset.theme === "dark" : !matchMedia("(prefers-color-scheme: light)").matches;
      root.dataset.theme = dark ? "light" : "dark";
      try { localStorage.setItem("theme", root.dataset.theme); } catch (e) {}
    }); });
  }

  // Меню персонажа и мобильное меню закрываются кликом мимо.
  document.addEventListener("click", function (e) {
    document.querySelectorAll("details.charmenu[open]").forEach(function (d) { if (!d.contains(e.target)) d.removeAttribute("open"); });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { var t = document.getElementById("nav-toggle"); if (t) t.checked = false; }
  });

  // Вкладки (личный кабинет): без JS видны все разделы подряд; ссылка /me#notify открывает нужную вкладку.
  document.querySelectorAll("[data-tabs]").forEach(function (box) {
    var panels = box.querySelectorAll(".tab-panel"), links = box.querySelectorAll("[data-tab]");
    function show() {
      var id = location.hash.slice(1), target = id && document.getElementById(id), panel = panels[0];
      panels.forEach(function (p) { if (target && (p === target || p.contains(target))) panel = p; });
      panels.forEach(function (p) { p.classList.toggle("on", p === panel); });
      links.forEach(function (a) {
        var on = a.dataset.tab === panel.id;
        a.classList.toggle("on", on);
        if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
      });
      if (target && target !== panel) target.focus && target.focus();
    }
    box.classList.add("tabs-js");
    links.forEach(function (a) { a.addEventListener("click", function (e) {
      e.preventDefault(); history.replaceState(null, "", "#" + a.dataset.tab); show();
    }); });
    window.addEventListener("hashchange", show);
    show();
  });

  initClock();
  initTheme();
  initPush();
  bind(document);
  if (document.querySelector("[data-live]")) {
    setInterval(refresh, LIVE_MS);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) refresh(); });
  }
})();
