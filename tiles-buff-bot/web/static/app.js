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
        document.querySelectorAll("[data-nick]").forEach(function (row) {
          row.hidden = q && row.dataset.nick.indexOf(q) === -1;
        });
      });
    }

    root.querySelectorAll("[data-refresh-now]").forEach(function (btn) {
      btn.addEventListener("click", function () { refresh(true); });
    });
  }

  // Автообновление данных: каждые N секунд страница тихо подтягивает свежую версию
  // и заменяет содержимое, если человек сейчас ничего не вводит и вкладка открыта.
  var every = parseInt(document.body.dataset.refresh || "0", 10) * 1000;
  var last = Date.now();
  var loading = false;

  function busy() {
    return document.querySelector("main input:focus, main select:focus, main textarea:focus, main details[open]");
  }

  function refresh(force) {
    if (loading || (!force && (document.hidden || busy()))) return;
    loading = true;
    fetch(location.href, { credentials: "same-origin", cache: "no-store" })
      .then(function (r) {
        if (!r.ok || r.redirected) throw new Error("skip");
        return r.text();
      })
      .then(function (html) {
        var fresh = new DOMParser().parseFromString(html, "text/html").querySelector("main");
        var current = document.querySelector("main");
        if (!fresh || !current) return;
        fresh.classList.add("no-anim");
        current.replaceWith(fresh);
        last = Date.now();
        bind(fresh);
      })
      .catch(function () {})
      .then(function () { loading = false; });
  }

  bind(document);
  if (every > 0) {
    setInterval(function () { refresh(false); }, every);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && Date.now() - last >= every) refresh(false);
    });
  }
})();
