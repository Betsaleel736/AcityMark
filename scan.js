// Student scan page: read a QR code with the camera, then go to the attendance page on THIS site.
(function () {
  "use strict";
  var startBtn = document.getElementById("start-scan");
  var stopBtn = document.getElementById("stop-scan");
  var message = document.getElementById("scan-message");
  var scanner = null;
  var running = false;

  function say(kind, text) { message.className = "notice " + kind; message.textContent = text; message.hidden = false; }

  // Accept a full attendance link or a bare token. We only ever navigate to our own /attend/ path.
  function extractToken(text) {
    var m = String(text).match(/\/attend\/([A-Za-z0-9_-]{20,64})/);
    if (m) { return m[1]; }
    var raw = String(text).trim();
    return /^[A-Za-z0-9_-]{20,64}$/.test(raw) ? raw : null;
  }

  function go(token) { window.location.assign("/attend/" + encodeURIComponent(token)); }

  function stop() {
    if (!scanner || !running) { return Promise.resolve(); }
    running = false;
    return scanner.stop().catch(function () {}).then(function () {
      startBtn.hidden = false; stopBtn.hidden = true;
    });
  }

  startBtn.addEventListener("click", function () {
    message.hidden = true;
    if (typeof Html5Qrcode === "undefined") {
      say("warn", "The scanner could not load (it needs internet access). Paste the attendance link below instead.");
      return;
    }
    if (!window.isSecureContext) {
      say("warn", "Browsers only allow the camera on HTTPS pages. Open the QR code with your phone's camera app, or paste the link below.");
      return;
    }
    scanner = scanner || new Html5Qrcode("reader");
    scanner.start({ facingMode: "environment" }, { fps: 10, qrbox: { width: 240, height: 240 } },
      function (decoded) {
        var token = extractToken(decoded);
        if (token) { stop().then(function () { go(token); }); }
        else { say("warn", "That QR code is not an AcityMark attendance code."); }
      }, function () { /* no code in view yet */ })
      .then(function () { running = true; startBtn.hidden = true; stopBtn.hidden = false; })
      .catch(function () {
        say("warn", "Could not open the camera. Allow camera access in your browser settings, or paste the link below.");
      });
  });

  stopBtn.addEventListener("click", stop);

  document.getElementById("manual-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var token = extractToken(document.getElementById("manual").value);
    if (token) { go(token); } else { say("warn", "That does not look like an AcityMark attendance link."); }
  });
})();
