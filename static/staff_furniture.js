// Staff Portal: load a job's furniture list the first time "View Furniture List" is opened.
(function () {
  document.addEventListener("toggle", function (event) {
    var box = event.target;
    if (!box.classList || !box.classList.contains("staff-furniture-details") || !box.open) return;
    if (box.getAttribute("data-loaded")) return;
    box.setAttribute("data-loaded", "1");
    var panel = box.querySelector(".staff-furniture-panel");
    fetch(box.getAttribute("data-panel-url"), { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
      .then(function (res) {
        if (!res.ok) throw new Error(String(res.status));
        return res.text();
      })
      .then(function (html) { panel.innerHTML = html; })
      .catch(function () {
        box.removeAttribute("data-loaded");
        var p = panel.querySelector(".staff-furniture-loading");
        if (p) p.firstChild.textContent = "Could not load. ";
      });
  }, true);
})();
