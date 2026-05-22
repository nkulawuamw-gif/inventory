(function () {
    'use strict';

    var ws = null;
    var reconnect = null;
    var currentCallId = null;
    var audioCtx = null;
    var osc1 = null;
    var osc2 = null;
    var gain = null;
    var pollTimer = null;
    var shown = false;

    function csrf() {
        var el = document.querySelector('[name=csrfmiddlewaretoken]');
        return el ? el.value : (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '';
    }

    function ringStart() {
        try {
            ringStop();
            audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            gain = audioCtx.createGain();
            gain.gain.value = 0.12;
            gain.connect(audioCtx.destination);
            osc1 = audioCtx.createOscillator();
            osc1.type = 'sine';
            osc1.frequency.value = 440;
            osc1.connect(gain);
            osc2 = audioCtx.createOscillator();
            osc2.type = 'sine';
            osc2.frequency.value = 480;
            osc2.connect(gain);
            osc1.start();
            osc2.start();
        } catch (e) {}
    }

    function ringStop() {
        try { if (osc1) { osc1.stop(); osc1 = null; } if (osc2) { osc2.stop(); osc2 = null; } if (audioCtx) { audioCtx.close(); audioCtx = null; } } catch (e) {}
    }

    function showModal(data) {
        if (shown) return;
        shown = true;
        currentCallId = data.call_id;
        var nameEl = document.getElementById('incomingCallerName');
        var typeEl = document.getElementById('incomingCallType');
        var ansBtn = document.getElementById('answerCallBtn');
        if (nameEl) nameEl.textContent = data.caller;
        if (typeEl) typeEl.textContent = data.call_type === 'video' ? 'Incoming video call' : 'Incoming voice call';
        if (ansBtn) ansBtn.href = '/calls/' + data.call_id + '/';
        var modalEl = document.getElementById('incomingCallModal');
        if (modalEl) {
            try { var m = bootstrap.Modal.getInstance(modalEl); if (m) m.hide(); } catch (e) {}
            var modal = new bootstrap.Modal(modalEl, { backdrop: 'static', keyboard: false });
            modal.show();
            ringStart();
        }
    }

    function hideModal() {
        shown = false;
        ringStop();
        currentCallId = null;
        var modalEl = document.getElementById('incomingCallModal');
        if (modalEl) {
            var m = bootstrap.Modal.getInstance(modalEl);
            if (m) m.hide();
        }
    }

    function decline() {
        ringStop();
        if (currentCallId) {
            var x = new XMLHttpRequest();
            x.open('POST', '/calls/' + currentCallId + '/end/', true);
            x.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded');
            x.send('csrfmiddlewaretoken=' + encodeURIComponent(csrf()));
        }
        hideModal();
    }

    window.stopCallUI = function () {
        console.log("Call ended");
        if (window.callSocket) {
            window.callSocket.close();
            window.callSocket = null;
        }
        var el = document.getElementById('callModal');
        if (el) el.style.display = 'none';
        hideModal();
    };

    window.endCall = function () {
        if (window.callSocket && window.callSocket.readyState === 1) {
            window.callSocket.send(JSON.stringify({
                type: "end_call"
            }));
        }
        stopCallUI();
    };

    function wsConnect() {
        if (ws && ws.readyState === WebSocket.OPEN) return;
        try {
            ws = new WebSocket("wss://" + window.location.host + "/ws/calls/");
        } catch (e) { schedule(); return; }
        window.callSocket = ws;
        window.currentCallSocket = ws;
        ws.onopen = function () { if (reconnect) { clearTimeout(reconnect); reconnect = null; } };
        ws.onmessage = function (e) {
            try {
                var d = JSON.parse(e.data);
                if (d.event === "end_call") {
                    if (window.stopCallUI) window.stopCallUI();
                } else if (d.type === 'incoming_call' && d.caller !== 'CALL_ENDED') {
                    showModal(d);
                } else if (d.caller === 'CALL_ENDED' || d.type === 'call_ended') {
                    hideModal();
                }
            } catch (err) {}
        };
        ws.onclose = function () { ws = null; schedule(); };
        ws.onerror = function () { try { ws.close(); } catch (e) {} };
    }

    function schedule() {
        if (!reconnect) reconnect = setTimeout(function () { reconnect = null; wsConnect(); }, 3000);
    }

    function poll() {
        fetch('/calls/incoming/', { headers: { 'X-CSRFToken': csrf() } })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (d.ringing && !shown) showModal(d);
                else if (!d.ringing && shown) hideModal();
            })
            .catch(function () {});
    }

    document.addEventListener('DOMContentLoaded', function () {
        var db = document.getElementById('declineCallBtn');
        if (db) db.addEventListener('click', decline);
        if (document.querySelector('[name=csrfmiddlewaretoken]')) {
            wsConnect();
            poll();
            pollTimer = setInterval(poll, 5000);
        }
    });

    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) {
            if (!ws || ws.readyState === WebSocket.CLOSED) wsConnect();
            poll();
            if (!pollTimer) pollTimer = setInterval(poll, 5000);
        }
    });
})();
