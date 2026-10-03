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

  bind(document);
  if (document.querySelector("[data-live]")) {
    setInterval(refresh, LIVE_MS);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) refresh(); });
  }
})();
