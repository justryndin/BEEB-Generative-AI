// Сервис-воркер: показывает уведомления и открывает нужную страницу по нажатию.
self.addEventListener("push", function (event) {
  var data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { title: "Бафы союза", body: event.data && event.data.text() }; }
  event.waitUntil(self.registration.showNotification(data.title || "Бафы союза", {
    body: data.body || "",
    tag: data.tag || undefined,
    icon: "/static/icon-192.png",
    badge: "/static/icon-192.png",
    data: { url: data.url || "/" }
  }));
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (list) {
    for (var i = 0; i < list.length; i++) {
      if ("focus" in list[i]) { list[i].navigate(url); return list[i].focus(); }
    }
    return clients.openWindow(url);
  }));
});
