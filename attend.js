// Student attendance page: get the location, send it to the server, show the server's decision.
(function () {
  "use strict";
  var btn = document.getElementById("mark-btn");
  var out = document.getElementById("result");
  if (!btn) { return; }
  var idleText = btn.textContent;

  function show(kind, title, message) {
    out.className = "notice " + kind;
    out.textContent = "";
    var h = document.createElement("strong");
    h.textContent = title;
    var p = document.createElement("p");
    p.textContent = message;
    out.appendChild(h);
    out.appendChild(p);
    out.hidden = false;
  }

  function reset() { btn.disabled = false; btn.textContent = idleText; }

  function send(position) {
    btn.textContent = "Checking with the server...";
    fetch("/api/attendance", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": window.AcityMark.csrf() },
      body: JSON.stringify({
        token: btn.dataset.token,
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
        accuracy: position.coords.accuracy
      })
    }).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (data) { return { res: res, data: data }; });
    }).then(function (r) {
      var data = r.data;
      if (data && data.ok) {
        show("ok", "Attendance approved", data.message);
        btn.hidden = true;
      } else if (data) {
        show(data.retry ? "warn" : "bad", data.retry ? "Not approved yet" : "Attendance not approved", data.message);
        if (data.retry) { reset(); } else { btn.hidden = true; }
      } else {
        show("bad", "Something went wrong", "The server sent an unexpected reply. Reload the page and try again.");
        reset();
      }
    }).catch(function () {
      show("bad", "No connection", "Could not reach the server. Check your internet connection and try again.");
      reset();
    });
  }

  btn.addEventListener("click", function () {
    out.hidden = true;
    if (!window.isSecureContext) {
      show("bad", "Secure connection needed", "Browsers only share location on HTTPS pages. Ask your lecturer for the secure (https) link.");
      return;
    }
    if (!navigator.geolocation) {
      show("bad", "Location not available", "This browser cannot share your location. Try another browser.");
      return;
    }
    btn.disabled = true;
    btn.textContent = "Getting your location...";
    navigator.geolocation.getCurrentPosition(send, function (err) {
      if (err.code === 1) {
        show("warn", "Location permission denied", "Allow location access for this site in your browser settings, then press the button again.");
      } else if (err.code === 3) {
        show("warn", "Location timed out", "Your phone took too long to find you. Move near a window or outdoors and try again.");
      } else {
        show("warn", "Location unavailable", "Your device could not determine its location. Turn on location services and try again.");
      }
      reset();
    }, { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 });
  });
})();
