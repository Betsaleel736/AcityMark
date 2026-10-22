// Lecturer session page: countdown plus live attendance list.
(function () {
  "use strict";
  var root = document.getElementById("live");
  if (!root) { return; }
  var countdown = document.getElementById("countdown");
  var label = document.getElementById("status-label");
  var roster = document.getElementById("roster");
  var status = root.dataset.status;
  var expiresAt = Date.now() + Number(root.dataset.seconds) * 1000;

  function pad(n) { return (n < 10 ? "0" : "") + n; }

  function drawCountdown() {
    if (status !== "active") { countdown.textContent = "--:--"; return; }
    var left = Math.max(0, Math.round((expiresAt - Date.now()) / 1000));
    countdown.textContent = pad(Math.floor(left / 60)) + ":" + pad(left % 60);
    countdown.classList.toggle("low", left <= 30);
    if (left === 0) { finish("expired"); }
  }

  function finish(newStatus) {
    status = newStatus;
    label.textContent = newStatus === "closed" ? "Session closed" : "Session expired";
    countdown.textContent = "--:--";
    countdown.classList.remove("low");
    var qr = document.getElementById("qr");
    if (qr) { qr.hidden = true; }
  }

  function refresh() {
    fetch(root.dataset.url, { headers: { Accept: "application/json" }, credentials: "same-origin" })
      .then(function (res) { if (!res.ok) { throw new Error("bad status"); } return res.json(); })
      .then(function (data) {
        roster.innerHTML = data.rows_html;  // HTML rendered and escaped by the server
        window.AcityMark.localizeTimes(roster);
        Object.keys(data.stats).forEach(function (key) {
          var el = root.querySelector('[data-stat="' + key + '"]');
          if (el) { el.textContent = key === "rate" ? data.stats[key] + "%" : data.stats[key]; }
        });
        if (data.status === "active") { expiresAt = Date.now() + data.seconds_left * 1000; }
        else if (status === "active") { finish(data.status); }
      })
      .catch(function () { /* keep the last list; try again on the next tick */ });
  }

  drawCountdown();
  setInterval(drawCountdown, 1000);
  setInterval(refresh, 5000);
  refresh();
})();
