// Show the student ID field for students and the invite code field for lecturers.
(function () {
  "use strict";
  function update() {
    var checked = document.querySelector('input[name="role"]:checked');
    var role = checked ? checked.value : "student";
    document.querySelectorAll("[data-role-field]").forEach(function (el) {
      el.hidden = el.getAttribute("data-role-field") !== role;
    });
  }
  document.querySelectorAll('input[name="role"]').forEach(function (r) { r.addEventListener("change", update); });
  update();
})();
