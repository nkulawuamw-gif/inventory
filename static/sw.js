self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', () => self.clients.claim());

self.addEventListener('push', function (event) {
    let data = {};
    try { data = event.data.json(); } catch (e) {}

    const title = data.title || 'Notification';
    const options = {
        body: data.body || '',
        icon: data.icon || '/static/favicon.ico',
        badge: '/static/favicon.ico',
        vibrate: [200, 100, 200],
        data: data.data || {},
    };

    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', function (event) {
    event.notification.close();
    const url = event.notification.data.url || '/';
    event.waitUntil(clients.openWindow(url));
});
