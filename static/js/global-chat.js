// ---------------- SERVICE WORKER + PUSH NOTIFICATIONS ----------------
if ('serviceWorker' in navigator && 'Notification' in window) {
    navigator.serviceWorker.register('/static/sw.js');

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

// ---------------- PRESENCE (REAL-TIME ONLINE/OFFLINE) ----------------
(function() {
    var presenceSocket = null;

    function connectPresence() {
        try {
            presenceSocket = new WebSocket("wss://" + window.location.host + "/ws/presence/");
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

    function escapeHtml(s) {
        var d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

    document.addEventListener('DOMContentLoaded', function() {
        if (document.querySelector('[name=csrfmiddlewaretoken]')) {
            connectPresence();
            connectChat();
        }
    });
})();

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
function showToastNotification(sender, message) {
    var container = document.getElementById('toastContainer');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toastContainer';
        container.style.cssText = 'position:fixed;bottom:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:8px;max-width:350px;';
        document.body.appendChild(container);
    }
    var toast = document.createElement('div');
    toast.style.cssText = 'background:#2c3e50;color:#fff;padding:12px 16px;border-radius:8px;box-shadow:0 4px 12px rgba(0,0,0,0.3);cursor:pointer;animation:fadeInUp 0.3s ease;font-size:0.9rem;';
    toast.innerHTML = '<strong style="display:block;margin-bottom:4px;">' + escapeHtml(sender) + '</strong><span style="color:#ccc;">' + escapeHtml(message) + '</span>';
    toast.onclick = function() { window.location.href = '/chat/'; };
    container.appendChild(toast);
    setTimeout(function() {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s';
        setTimeout(function() { if (toast.parentNode) toast.parentNode.removeChild(toast); }, 300);
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
            updatePageTitle(count);
        })
        .catch(function() {});
}

// ---------------- CHAT ----------------
var chatSocket = null;

function connectChat() {
    chatSocket = new WebSocket("wss://" + window.location.host + "/ws/chat/");

    chatSocket.onmessage = function(e) {
        var data;
        try { data = JSON.parse(e.data); } catch (err) { return; }

        if (data.type === 'chat_message') {
            var inModalChat = typeof modalChatUserId !== 'undefined' && modalChatUserId && data.sender_id === modalChatUserId;
            if (data.sender_id && data.sender_id !== currentChatUserId && !inModalChat) {
                showBrowserNotification(data.sender, data.message, '/chat/');
                showToastNotification(data.sender, data.message);
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
    };

    chatSocket.onclose = function() { setTimeout(connectChat, 2000); };
    chatSocket.onerror = function() { try { chatSocket.close(); } catch(e) {} };
}

// ---------------- POLL UNREAD BADGE ----------------
updateUnreadBadge();
setInterval(updateUnreadBadge, 15000);
