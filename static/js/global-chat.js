// ---------------- SERVICE WORKER + PUSH NOTIFICATIONS ----------------
if ('serviceWorker' in navigator && 'Notification' in window) {
    navigator.serviceWorker.register('/static/sw.js').catch(function() {});

    function requestPushPermission() {
        if (Notification.permission === 'default') {
            Notification.requestPermission();
        }
    }

    function showBrowserNotification(title, body, url) {
        if (Notification.permission === 'granted') {
            navigator.serviceWorker.ready.then(function(reg) {
                reg.showNotification(title, {
                    body: body,
                    icon: '/static/favicon.ico',
                    badge: '/static/favicon.ico',
                    vibrate: [200, 100, 200],
                    data: { url: url || '/' }
                });
            });
        }
    }

    requestPushPermission();
}

// ---------------- NOTIFICATION SOUND ----------------
var audioCtx = null;

function playNotificationSound() {
    try {
        if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        var osc = audioCtx.createOscillator();
        var gain = audioCtx.createGain();
        osc.connect(gain);
        gain.connect(audioCtx.destination);
        osc.frequency.value = 880;
        osc.type = 'sine';
        gain.gain.setValueAtTime(0.15, audioCtx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.3);
        osc.start(audioCtx.currentTime);
        osc.stop(audioCtx.currentTime + 0.3);
    } catch (e) {}
}

// ---------------- TOAST NOTIFICATION ----------------
function showNotificationToast(senderName, message) {
    var container = document.getElementById('toastContainer');
    if (!container) return;

    var toast = document.createElement('div');
    toast.className = 'toast-notification toast-info';
    toast.innerHTML = '<span class="toast-icon"><i class="bi bi-bell-fill"></i></span><span class="toast-msg"><strong>' + escapeHtml(senderName) + '</strong><br><span style="font-size:0.85rem;opacity:0.9;">' + escapeHtml(message) + '</span></span><button class="toast-close" onclick="this.closest(\'.toast-notification\').remove()">&times;</button>';
    toast.onclick = function(e) {
        if (e.target.tagName !== 'BUTTON') {
            window.location.href = '/chat/';
        }
    };
    toast.style.cursor = 'pointer';
    container.appendChild(toast);
    playNotificationSound();
    setTimeout(function() {
        toast.classList.add('removing');
        setTimeout(function() { if (toast.parentNode) toast.remove(); }, 300);
    }, 5000);
}

var styleAdded = false;
function addToastAnimation() {
    if (styleAdded) return;
    styleAdded = true;
    var s = document.createElement('style');
    s.textContent = '@keyframes fadeInUp{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:translateY(0)}}';
    document.head.appendChild(s);
}

// ---------------- PAGE TITLE HELPER ----------------
var originalTitle = document.title;

function updatePageTitle(count) {
    if (count > 0) {
        document.title = '(' + count + ') ' + originalTitle.replace(/^\(\d+\) /, '');
    } else {
        document.title = originalTitle.replace(/^\(\d+\) /, '');
    }
}

// ---------------- NOTIFICATION BADGE (bell) ----------------
function updateNotificationBadge() {
    fetch('/notifications/unread/')
        .then(function(r) { return r.json(); })
        .then(function(d) {
            var badge = document.getElementById('notificationBadge');
            var count = d.count || 0;
            if (badge) {
                if (count > 0) {
                    badge.textContent = count > 99 ? '99+' : count;
                    badge.style.display = '';
                } else {
                    badge.style.display = 'none';
                }
            }
            var totalUnread = count;
            var chatBadge = document.getElementById('sidebarChatBadge');
            var chatCount = parseInt(chatBadge ? chatBadge.textContent : '0', 10);
            if (isNaN(chatCount)) chatCount = 0;
            updatePageTitle(totalUnread + chatCount);
        })
        .catch(function() {});
}

// ---------------- NOTIFICATION DROPDOWN ----------------
function toggleNotificationPanel() {
    var panel = document.getElementById('notificationPanel');
    if (!panel) return;
    var isVisible = panel.style.display !== 'none';
    if (isVisible) {
        panel.style.display = 'none';
    } else {
        loadNotifications();
        panel.style.display = 'block';
    }
}

function loadNotifications() {
    var panel = document.getElementById('notificationPanel');
    if (!panel) return;
    panel.innerHTML = '<div class="text-center py-3"><div class="spinner-border spinner-border-sm text-light" role="status"></div> Loading...</div>';

    fetch('/notifications/')
        .then(function(r) { return r.json(); })
        .then(function(d) {
            var notifs = d.notifications || [];
            panel.innerHTML = '';
            if (notifs.length === 0) {
                panel.innerHTML = '<div class="text-center py-4 text-white-50"><i class="bi bi-bell-slash" style="font-size:2rem;display:block;margin-bottom:8px;"></i>No notifications</div>';
                return;
            }
            var list = document.createElement('div');
            list.className = 'notification-list';
            notifs.forEach(function(n) {
                var item = document.createElement('div');
                item.className = 'notification-item' + (n.is_read ? '' : ' unread');
                item.dataset.id = n.id;
                item.onclick = function() {
                    markNotifRead(n.id);
                    if (n.link) window.location.href = n.link;
                };
                var typeIcon = 'bi-bell-fill';
                if (n.type === 'message') typeIcon = 'bi-chat-dots-fill';
                else if (n.type === 'system') typeIcon = 'bi-gear-fill';
                else if (n.type === 'transfer') typeIcon = 'bi-arrow-left-right';
                else if (n.type === 'sale') typeIcon = 'bi-cart-fill';
                item.innerHTML = '<div class="notif-icon"><i class="bi ' + typeIcon + '"></i></div><div class="notif-content"><div class="notif-title">' + escapeHtml(n.title) + '</div><div class="notif-message">' + escapeHtml(n.message) + '</div><div class="notif-time">' + formatTimeAgo(n.created_at) + '</div></div>';
                list.appendChild(item);
            });
            panel.appendChild(list);
            if (d.unread_count > 0) {
                var markBtn = document.createElement('div');
                markBtn.className = 'notification-mark-all';
                markBtn.innerHTML = '<button class="btn btn-sm btn-outline-light w-100" onclick="markAllNotifRead()"><i class="bi bi-check2-all"></i> Mark all as read</button>';
                panel.appendChild(markBtn);
            }
        })
        .catch(function() {
            panel.innerHTML = '<div class="text-center py-3 text-danger">Failed to load notifications</div>';
        });
}

function escapeHtml(s) {
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

function formatTimeAgo(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    var now = new Date();
    var diff = Math.floor((now - d) / 1000);
    if (diff < 60) return 'just now';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    if (diff < 604800) return Math.floor(diff / 86400) + 'd ago';
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function markNotifRead(id) {
    fetch('/notifications/' + id + '/mark-read/', {
        method: 'POST',
        headers: {
            'X-CSRFToken': getCSRFToken(),
            'Content-Type': 'application/x-www-form-urlencoded',
        },
    }).then(function() {
        updateNotificationBadge();
        if (notifSocket && notifSocket.readyState === WebSocket.OPEN) {
            notifSocket.send(JSON.stringify({ type: 'mark_read', notification_id: id }));
        }
    }).catch(function() {});
}

function markAllNotifRead() {
    fetch('/notifications/mark-all-read/', {
        method: 'POST',
        headers: {
            'X-CSRFToken': getCSRFToken(),
            'Content-Type': 'application/x-www-form-urlencoded',
        },
    }).then(function() {
        updateNotificationBadge();
        var items = document.querySelectorAll('.notification-item.unread');
        items.forEach(function(el) { el.classList.remove('unread'); });
        document.querySelector('.notification-mark-all')?.remove();
        if (notifSocket && notifSocket.readyState === WebSocket.OPEN) {
            notifSocket.send(JSON.stringify({ type: 'mark_all_read' }));
        }
    }).catch(function() {});
}

function getCSRFToken() {
    var el = document.querySelector('[name=csrfmiddlewaretoken]');
    if (el) return el.value;
    var cookie = document.cookie.match('csrftoken=([^;]*)');
    return cookie ? cookie[1] : '';
}

// ---------------- UNREAD BADGE HELPER ----------------
function updateUnreadBadge() {
    fetch('/chat/unread/')
        .then(function(r) { return r.json(); })
        .then(function(d) {
            var sidebarBadge = document.getElementById('sidebarChatBadge');
            var navbarBadge = document.getElementById('navbarChatBadge');
            var count = d.count || 0;
            if (sidebarBadge) {
                sidebarBadge.textContent = count;
                sidebarBadge.style.display = count > 0 ? '' : 'none';
            }
            if (navbarBadge) {
                navbarBadge.textContent = count;
                navbarBadge.style.display = count > 0 ? '' : 'none';
            }
        })
        .catch(function() {});
}

// ---------------- PRESENCE (REAL-TIME ONLINE/OFFLINE) ----------------
var presenceSocket = null;
var chatSocket = null;
var notifSocket = null;

var chatConnected = false;

function connectAll() {
    connectPresence();
    connectChat();
    connectNotifications();
}

function connectPresence() {
    var url = (window.location.protocol === 'https:' ? 'wss:' : 'ws:') + '//' + window.location.host + '/ws/presence/';
    try {
        presenceSocket = new WebSocket(url);
    } catch (e) { setTimeout(connectPresence, 3000); return; }

    presenceSocket.onmessage = function(e) {
        try {
            var d = JSON.parse(e.data);
            if (d.type === 'presence_update') {
                updateOnlineStatus(d);
            }
        } catch (err) {}
    };

    presenceSocket.onclose = function() { setTimeout(connectPresence, 3000); };
    presenceSocket.onerror = function() { try { presenceSocket.close(); } catch(e) {} };
}

function updateOnlineStatus(d) {
    var panel = document.getElementById('onlineUsersPanel');
    var countEl = document.getElementById('onlineCount');
    var badge = document.getElementById('onlineBadge');
    if (!countEl) return;

    var current = parseInt(countEl.textContent || '0', 10);

    if (d.is_online) {
        countEl.textContent = current + 1;
        if (panel) {
            var existing = panel.querySelector('[data-uid="' + d.user_id + '"]');
            if (!existing) {
                var div = document.createElement('div');
                div.className = 'px-3 py-2 border-bottom border-secondary d-flex align-items-center gap-2';
                div.setAttribute('data-uid', d.user_id);
                div.innerHTML = '<span class="online-dot"></span><span class="text-light small">' + escapeHtml(d.display_name) + '</span>';
                panel.appendChild(div);
            }
        }
    } else {
        countEl.textContent = Math.max(0, current - 1);
        if (panel) {
            var el = panel.querySelector('[data-uid="' + d.user_id + '"]');
            if (el) el.remove();
        }
    }

    if (badge) badge.style.display = (parseInt(countEl.textContent) > 0) ? '' : 'none';
}

function connectChat() {
    var url = (window.location.protocol === 'https:' ? 'wss:' : 'ws:') + '//' + window.location.host + '/ws/chat/';
    try {
        chatSocket = new WebSocket(url);
    } catch (e) {
        chatConnected = false;
        setTimeout(connectChat, 3000);
        return;
    }

    chatSocket.onopen = function() {
        chatConnected = true;
        // notify any chat page listeners
        var evt = document.createEvent('Event');
        evt.initEvent('chat-connected', true, true);
        document.dispatchEvent(evt);
    };

    chatSocket.onmessage = function(e) {
        var data;
        try { data = JSON.parse(e.data); } catch (err) { return; }

        if (data.type === 'chat_message') {
            if (data.sender_id && data.sender_id !== currentChatUserId) {
                showBrowserNotification(data.sender_name || data.sender, data.message, '/chat/');
                showNotificationToast(data.sender_name || data.sender, data.message);
                playNotificationSound();
                addToastAnimation();
            }
            updateUnreadBadge();
            document.dispatchEvent(new CustomEvent('chat-message', { detail: data }));
        }

        if (data.type === 'message_status') {
            document.dispatchEvent(new CustomEvent('chat-status', { detail: data }));
        }

        if (data.type === 'unread_update') {
            var sidebarBadge = document.getElementById('sidebarChatBadge');
            var navbarBadge = document.getElementById('navbarChatBadge');
            var count = data.count || 0;
            if (sidebarBadge) {
                sidebarBadge.textContent = count;
                sidebarBadge.style.display = count > 0 ? '' : 'none';
            }
            if (navbarBadge) {
                navbarBadge.textContent = count;
                navbarBadge.style.display = count > 0 ? '' : 'none';
            }
            updatePageTitle(count);
        }

        if (data.type === 'typing') {
            document.dispatchEvent(new CustomEvent('chat-typing', { detail: data }));
        }

        if (data.type === 'conversations') {
            document.dispatchEvent(new CustomEvent('chat-conversations', { detail: data }));
        }
    };

    chatSocket.onclose = function() {
        chatConnected = false;
        setTimeout(connectChat, 3000);
    };
    chatSocket.onerror = function() { try { chatSocket.close(); } catch(e) {} };
}

function connectNotifications() {
    var url = (window.location.protocol === 'https:' ? 'wss:' : 'ws:') + '//' + window.location.host + '/ws/notifications/';
    try {
        notifSocket = new WebSocket(url);
    } catch (e) { setTimeout(connectNotifications, 3000); return; }

    notifSocket.onmessage = function(e) {
        var data;
        try { data = JSON.parse(e.data); } catch (err) { return; }

        if (data.type === 'new_notification') {
            showNotificationToast(data.sender_name || 'System', data.message);
            showBrowserNotification(data.title, data.message, data.link || '/chat/');
            updateNotificationBadge();
        }

        if (data.type === 'notification_count') {
            var badge = document.getElementById('notificationBadge');
            var count = data.count || 0;
            if (badge) {
                if (count > 0) {
                    badge.textContent = count > 99 ? '99+' : count;
                    badge.style.display = '';
                } else {
                    badge.style.display = 'none';
                }
            }
        }
    };

    notifSocket.onclose = function() { setTimeout(connectNotifications, 3000); };
    notifSocket.onerror = function() { try { notifSocket.close(); } catch(e) {} };
}

// ---------------- INIT ----------------
function initApp() {
    if (document.querySelector('[name=csrfmiddlewaretoken]')) {
        connectAll();
    }
    updateUnreadBadge();
    updateNotificationBadge();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initApp);
} else {
    initApp();
}

setInterval(updateUnreadBadge, 15000);
setInterval(updateNotificationBadge, 30000);

// Close notification panel on outside click
document.addEventListener('click', function(e) {
    var panel = document.getElementById('notificationPanel');
    var bell = document.getElementById('notificationBell');
    if (panel && bell && !bell.contains(e.target) && !panel.contains(e.target)) {
        panel.style.display = 'none';
    }
});
