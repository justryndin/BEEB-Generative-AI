// Немного удобства поверх обычных форм. Сайт работает и без JavaScript.
(function () {
  // Кнопки «−» и «+» у полей дней/часов/минут.
  document.querySelectorAll("[data-step]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var input = document.getElementById(btn.dataset.target);
      var max = parseInt(input.max || "999", 10);
      var value = (parseInt(input.value || "0", 10) || 0) + parseInt(btn.dataset.step, 10);
      input.value = Math.max(0, Math.min(max, value));
    });
  });

  // Подтверждение важных действий.
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (!window.confirm(form.dataset.confirm)) e.preventDefault();
    });
  });

  // Скопировать ник, чтобы вставить в поиск в игре.
  document.querySelectorAll("[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var text = btn.dataset.copy;
      var done = function () { var old = btn.textContent; btn.textContent = "✅ Скопировано"; setTimeout(function () { btn.textContent = old; }, 1500); };
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, function () {});
    });
  });

  // Поиск по игрокам в управлении.
  var search = document.getElementById("player-search");
  if (search) {
    search.addEventListener("input", function () {
      var q = search.value.trim().toLowerCase();
      document.querySelectorAll("[data-nick]").forEach(function (row) {
        row.hidden = q && row.dataset.nick.indexOf(q) === -1;
      });
    });
  }

  // Очередь и главная сами обновляются раз в 2 минуты, если ничего не вводишь.
  if (document.body.dataset.autorefresh) {
    setTimeout(function () {
      if (!document.querySelector("input:focus, select:focus")) location.reload();
    }, 120000);
  }
})();
