(function() {
    'use strict';

    var ws = null;
    var wsReconnectTimer = null;
    var currentCallId = null;
    var ringingAudioCtx = null;
    var ringingOsc1 = null;
    var ringingOsc2 = null;
    var ringingGain = null;
    var pollTimer = null;
    var modalShown = false;

    window.activeJitsiApi = null;

    function getCSRFToken() {
        var el = document.querySelector('[name=csrfmiddlewaretoken]');
        if (el) return el.value;
        var cookie = document.cookie.match('csrftoken=([^;]*)');
        return cookie ? cookie[1] : '';
    }

    function startRingingTone() {
        try {
            stopRingingTone();
            ringingAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
            ringingGain = ringingAudioCtx.createGain();
            ringingGain.gain.value = 0.12;
            ringingGain.connect(ringingAudioCtx.destination);

            ringingOsc1 = ringingAudioCtx.createOscillator();
            ringingOsc1.type = 'sine';
            ringingOsc1.frequency.value = 440;
            ringingOsc1.connect(ringingGain);

            ringingOsc2 = ringingAudioCtx.createOscillator();
            ringingOsc2.type = 'sine';
            ringingOsc2.frequency.value = 480;
            ringingOsc2.connect(ringingGain);

            ringingOsc1.start();
            ringingOsc2.start();
        } catch(e) {}
    }

    function stopRingingTone() {
        try {
            if (ringingOsc1) { ringingOsc1.stop(); ringingOsc1 = null; }
            if (ringingOsc2) { ringingOsc2.stop(); ringingOsc2 = null; }
            if (ringingAudioCtx) { ringingAudioCtx.close(); ringingAudioCtx = null; }
        } catch(e) {}
    }

    function disposeJitsiInstance() {
        if (window.activeJitsiApi) {
            try { window.activeJitsiApi.dispose(); } catch(e) {}
            window.activeJitsiApi = null;
        }
    }

    function showIncomingCallModal(data) {
        if (modalShown) return;
        modalShown = true;
        currentCallId = data.call_id;
        var nameEl = document.getElementById('incomingCallerName');
        var typeEl = document.getElementById('incomingCallType');
        var answerBtn = document.getElementById('answerCallBtn');
        if (nameEl) nameEl.textContent = data.caller;
        if (typeEl) typeEl.textContent = data.call_type === 'video' ? 'Incoming video call' : 'Incoming voice call';
        if (answerBtn) {
            disposeJitsiInstance();
            answerBtn.href = '/calls/' + data.call_id + '/';
        }

        var modalEl = document.getElementById('incomingCallModal');
        if (modalEl) {
            var modal = new bootstrap.Modal(modalEl, { backdrop: 'static', keyboard: false });
            modal.show();
            startRingingTone();
        }
    }

    function hideIncomingCallModal() {
        modalShown = false;
        stopRingingTone();
        currentCallId = null;
        var modalEl = document.getElementById('incomingCallModal');
        if (modalEl) {
            var modal = bootstrap.Modal.getInstance(modalEl);
            if (modal) modal.hide();
        }
    }

    function checkIncomingCallHTTP() {
        fetch('/calls/incoming/', {
            method: 'GET',
            headers: { 'X-CSRFToken': getCSRFToken() },
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.ringing && !modalShown) {
                showIncomingCallModal({
                    call_id: data.call_id,
                    caller: data.caller,
                    call_type: data.call_type,
                });
            } else if (!data.ringing && modalShown) {
                hideIncomingCallModal();
            }
        })
        .catch(function() {});
    }

    function connectWebSocket() {
        if (ws && ws.readyState === WebSocket.OPEN) return;

        var protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        var wsUrl = protocol + '//' + window.location.host + '/ws/calls/';

        ws = new WebSocket(wsUrl);

        ws.onopen = function() {
            if (wsReconnectTimer) {
                clearTimeout(wsReconnectTimer);
                wsReconnectTimer = null;
            }
        };

        ws.onmessage = function(e) {
            try {
                var data = JSON.parse(e.data);
                if (data.type === 'incoming_call' && data.caller !== 'CALL_ENDED') {
                    showIncomingCallModal(data);
                } else if (data.type === 'incoming_call' && data.caller === 'CALL_ENDED') {
                    hideIncomingCallModal();
                }
            } catch(err) {}
        };

        ws.onclose = function() {
            ws = null;
            if (!wsReconnectTimer) {
                wsReconnectTimer = setTimeout(connectWebSocket, 2000);
            }
        };

        ws.onerror = function() {
            ws.close();
        };
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

    function handleDecline() {
        stopRingingTone();
        if (currentCallId) {
            fetch('/calls/' + currentCallId + '/end/', {
                method: 'POST',
                headers: { 'X-CSRFToken': getCSRFToken() },
            }).catch(function() {});
        }
        hideIncomingCallModal();
    }

    document.addEventListener('DOMContentLoaded', function() {
        var declineBtn = document.getElementById('declineCallBtn');
        if (declineBtn) {
            declineBtn.addEventListener('click', handleDecline);
        }

        if (document.querySelector('[name=csrfmiddlewaretoken]')) {
            connectWebSocket();
            checkIncomingCallHTTP();
            pollTimer = setInterval(checkIncomingCallHTTP, 4000);
        }
    });

    document.addEventListener('visibilitychange', function() {
        if (!document.hidden) {
            if (!ws || ws.readyState === WebSocket.CLOSED) {
                connectWebSocket();
            }
            checkIncomingCallHTTP();
            if (!pollTimer) {
                pollTimer = setInterval(checkIncomingCallHTTP, 4000);
            }
        }
    });
})();
