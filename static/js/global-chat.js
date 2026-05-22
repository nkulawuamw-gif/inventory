(function() {
    'use strict';

    var ws = null;
    var wsReconnectTimer = null;
    var wsReconnectDelay = 1000;
    var knownMessageIds = new Set();

    function getCSRFToken() {
        var el = document.querySelector('[name=csrfmiddlewaretoken]');
        if (el) return el.value;
        var cookie = document.cookie.match('csrftoken=([^;]*)');
        return cookie ? cookie[1] : '';
    }

    function updateUnreadBadge(count) {
        var badge = document.getElementById('unreadBadge');
        var badgeMobile = document.getElementById('unreadBadgeMobile');
        var display = count > 0 ? (count > 9 ? '9+' : count) : null;
        if (badge) {
            if (display) { badge.textContent = display; badge.style.display = 'inline'; }
            else { badge.style.display = 'none'; }
        }
        if (badgeMobile) {
            if (display) { badgeMobile.textContent = display; badgeMobile.style.display = 'inline'; }
            else { badgeMobile.style.display = 'none'; }
        }
    }

    function showToast(message, type) {
        var container = document.getElementById('toastContainer');
        if (!container) return;
        type = type || 'info';
        var icons = { success: 'bi-check-circle-fill', error: 'bi-x-circle-fill', warning: 'bi-exclamation-circle-fill', info: 'bi-info-circle-fill' };
        var icon = icons[type] || icons.info;
        var toast = document.createElement('div');
        toast.className = 'toast-notification toast-' + type;
        toast.innerHTML = '<span class="toast-icon"><i class="bi ' + icon + '"></i></span><span class="toast-msg">' + message + '</span><button class="toast-close" onclick="this.closest(\'.toast-notification\').remove()">&times;</button>';
        container.appendChild(toast);
        setTimeout(function() {
            toast.classList.add('removing');
            setTimeout(function() { if (toast.parentNode) toast.remove(); }, 300);
        }, 4000);
    }

    function playNotificationSound() {
        try {
            var ctx = new (window.AudioContext || window.webkitAudioContext)();
            var g = ctx.createGain();
            g.gain.value = 0.08;
            g.connect(ctx.destination);
            var o = ctx.createOscillator();
            o.type = 'sine';
            o.frequency.value = 660;
            o.connect(g);
            o.start();
            o.stop(ctx.currentTime + 0.12);
            var o2 = ctx.createOscillator();
            o2.type = 'sine';
            o2.frequency.value = 880;
            o2.connect(g);
            o2.start(ctx.currentTime + 0.12);
            o2.stop(ctx.currentTime + 0.25);
        } catch(e) {}
    }

    function requestNotificationPermission() {
        if ('Notification' in window && Notification.permission === 'default') {
            Notification.requestPermission();
        }
    }

    function sendBrowserNotification(title, body, tag) {
        if (!('Notification' in window) || Notification.permission !== 'granted') return;
        try {
            var n = new Notification(title, { body: body, tag: tag || 'chat', icon: '/static/images/notification-icon.png' });
            setTimeout(function() { n.close(); }, 5000);
        } catch(e) {}
    }

    function connectWebSocket() {
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

        var protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        var wsUrl = protocol + '//' + window.location.host + '/ws/chat/';

        ws = new WebSocket(wsUrl);

        ws.onopen = function() {
            if (wsReconnectTimer) {
                clearTimeout(wsReconnectTimer);
                wsReconnectTimer = null;
            }
            wsReconnectDelay = 1000;
        };

        ws.onmessage = function(e) {
            try {
                var data = JSON.parse(e.data);
                handleServerEvent(data);
            } catch(err) {}
        };

        ws.onclose = function() {
            ws = null;
            scheduleReconnect();
        };

        ws.onerror = function() {
            ws.close();
        };
    }

    function scheduleReconnect() {
        if (wsReconnectTimer) return;
        wsReconnectTimer = setTimeout(function() {
            wsReconnectTimer = null;
            connectWebSocket();
        }, wsReconnectDelay);
        wsReconnectDelay = Math.min(wsReconnectDelay * 1.5, 15000);
    }

    function disconnectWebSocket() {
        if (wsReconnectTimer) {
            clearTimeout(wsReconnectTimer);
            wsReconnectTimer = null;
        }
        if (ws) {
            ws.onclose = null;
            ws.close();
            ws = null;
        }
    }

    function handleServerEvent(data) {
        switch (data.type) {
            case 'new_message':
                handleNewMessage(data);
                break;
            case 'unread_update':
                updateUnreadBadge(data.count);
                break;
            case 'typing':
                handleTyping(data);
                break;
            case 'messages_read':
                handleMessagesRead(data);
                break;
        }
    }

    function handleNewMessage(data) {
        if (knownMessageIds.has(data.id)) return;
        if (data.id) knownMessageIds.add(data.id);

        var isFromOther = data.sender !== undefined;

        document.dispatchEvent(new CustomEvent('chat:message', { detail: data }));

        if (isFromOther) {
            var senderLabel = data.sender_name || 'User';
            showToast(senderLabel + ': ' + (data.body || 'Voice note'), 'info');
            playNotificationSound();
            sendBrowserNotification(
                senderLabel,
                data.body || 'Sent a voice note',
                'chat-msg-' + data.id
            );
        }
    }

    function handleTyping(data) {
        document.dispatchEvent(new CustomEvent('chat:typing', { detail: data }));
    }

    function handleMessagesRead(data) {
        document.dispatchEvent(new CustomEvent('chat:read', { detail: data }));
    }

    function sendRaw(obj) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify(obj));
        }
    }

    window.ChatWS = {
        sendMessage: function(receiverId, body) {
            sendRaw({ action: 'send_message', receiver_id: receiverId, body: body });
        },
        sendTyping: function(receiverId) {
            sendRaw({ action: 'typing', receiver_id: receiverId });
        },
        stopTyping: function() {
            sendRaw({ action: 'typing', receiver_id: null });
        },
        markRead: function(userId) {
            sendRaw({ action: 'mark_read', user_id: userId });
        },
        requestUnread: function() {
            sendRaw({ action: 'request_unread' });
        }
    };

    document.addEventListener('DOMContentLoaded', function() {
        if (document.querySelector('[name=csrfmiddlewaretoken]')) {
            requestNotificationPermission();
            connectWebSocket();
        }
    });

    document.addEventListener('visibilitychange', function() {
        if (!document.hidden && (!ws || ws.readyState === WebSocket.CLOSED)) {
            connectWebSocket();
        }
        if (!document.hidden) {
            if (window.ChatWS) window.ChatWS.requestUnread();
        }
    });
})();
