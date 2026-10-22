// Shared helpers used on every page.
(function () {
  "use strict";

  function localizeTimes(root) {
    (root || document).querySelectorAll("time.local-time").forEach(function (el) {
      var d = new Date(el.getAttribute("datetime"));
      if (!isNaN(d)) {
        el.textContent = d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
      }
    });
  }

  window.AcityMark = {
    localizeTimes: localizeTimes,
    csrf: function () {
      var meta = document.querySelector('meta[name="csrf-token"]');
      return meta ? meta.content : "";
    }
  };

  localizeTimes();

  // Course page: fill latitude/longitude from this device's location.
  var useBtn = document.getElementById("use-location");
  if (useBtn) {
    var status = document.getElementById("location-status");
    useBtn.addEventListener("click", function () {
      if (!navigator.geolocation) { status.textContent = "This browser cannot share location."; return; }
      status.textContent = "Getting location...";
      navigator.geolocation.getCurrentPosition(function (pos) {
        document.getElementById("latitude").value = pos.coords.latitude.toFixed(6);
        document.getElementById("longitude").value = pos.coords.longitude.toFixed(6);
        status.textContent = "Location filled in (accuracy about " + Math.round(pos.coords.accuracy) + " m). Check it, then save.";
      }, function (err) {
        status.textContent = err.code === 1
          ? "Location permission was denied. Allow it in your browser, or type the coordinates."
          : "Could not get a location. Type the coordinates instead.";
      }, { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 });
    });
  }

  // Session page: copy the attendance link.
  var copyBtn = document.getElementById("copy-url");
  if (copyBtn) {
    copyBtn.addEventListener("click", function () {
      var input = document.getElementById("attend-url");
      input.select();
      (navigator.clipboard ? navigator.clipboard.writeText(input.value) : Promise.reject())
        .catch(function () { document.execCommand("copy"); })
        .then(function () { copyBtn.textContent = "Copied"; setTimeout(function () { copyBtn.textContent = "Copy"; }, 1500); });
    });
  }
})();
