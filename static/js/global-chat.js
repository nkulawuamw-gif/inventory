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
        }
    });
})();

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

// ---------------- CHAT ----------------
var chatSocket = null;

function connectChat() {
    chatSocket = new WebSocket("wss://" + window.location.host + "/ws/chat/");

    chatSocket.onmessage = function(e) {
        var data;
        try { data = JSON.parse(e.data); } catch (err) { return; }

        if (data.type === 'chat_message') {
            if (data.sender_id && data.sender_id !== currentChatUserId) {
                showBrowserNotification(data.sender, data.message, '/chat/');
            }
            updateUnreadBadge();
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
        }
    };

    chatSocket.onclose = function() { setTimeout(connectChat, 2000); };
}

connectChat();

// ---------------- CALLS ----------------
var callSocket = null;

function connectCalls() {
    callSocket = new WebSocket("wss://" + window.location.host + "/ws/calls/");

    callSocket.onmessage = function(e) {
        var data;
        try { data = JSON.parse(e.data); } catch (err) { return; }

        if (data.type === 'call_event' && data.event === 'end_call') {
            var modalEl = document.getElementById('incomingCallModal');
            if (modalEl) {
                var m = bootstrap.Modal.getInstance(modalEl);
                if (m) m.hide();
            }
            return;
        }

        if (data.incoming_call) {
            showBrowserNotification(
                data.caller || 'Incoming Call',
                data.call_type === 'video' ? 'Video call' : 'Audio call',
                '/calls/'
            );
            showCallPopup(data);
        }

        if (data.room_name) {
            openJitsi(data.room_name);
        }
    };

    callSocket.onclose = function() { setTimeout(connectCalls, 2000); };
}

connectCalls();

// ---------------- JITSI ----------------
function openJitsi(roomName) {
    var domain = "meet.jit.si";
    var options = {
        roomName: roomName,
        parentNode: document.getElementById("jitsi-container"),
        configOverwrite: {
            prejoinPageEnabled: false,
            disableDeepLinking: true
        },
        interfaceConfigOverwrite: {
            MOBILE_APP_PROMO: false
        }
    };

    if (window.jitsiApi) {
        window.jitsiApi.dispose();
    }
    window.jitsiApi = new JitsiMeetExternalAPI(domain, options);
}

// ---------------- POLL UNREAD BADGE ----------------
setInterval(updateUnreadBadge, 30000);
