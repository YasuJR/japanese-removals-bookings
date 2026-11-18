// WG Orders page: one WG Day (date, crew, truck once) with as many stops as needed.
// "+ Add Stop" adds a blank stop; the small "Delete stop" removes one.
(function () {
  var rows = document.getElementById("wg-stop-rows");
  var template = document.getElementById("wg-stop-template");
  var addBtn = document.getElementById("wg-stop-add");
  var countEl = document.getElementById("wg-stop-count");
  if (!rows) return;

  function stops() {
    return rows.querySelectorAll("[data-wg-stop]");
  }

  function renumber() {
    var labels = rows.querySelectorAll(".wg-stop-number");
    for (var i = 0; i < labels.length; i++) labels[i].textContent = String(i + 1);
    var n = stops().length;
    if (countEl) countEl.textContent = n + (n === 1 ? " stop" : " stops");
  }

  function hasContent(box) {
    var fields = box.querySelectorAll("input[type=text], input[type=tel], input[type=time], textarea");
    for (var i = 0; i < fields.length; i++) {
      if (fields[i].value.trim() && fields[i].name.indexOf("payment_type") === -1) return true;
    }
    return false;
  }

  rows.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-wg-delete-stop]");
    if (!btn) return;
    var box = btn.closest("[data-wg-stop]");
    if (!box) return;
    if (hasContent(box) && !window.confirm("Delete this stop? It is removed when you press Save WG Day.")) return;
    if (stops().length > 1) {
      box.remove();
    } else {
      box.querySelectorAll("input[type=text], input[type=tel], input[type=time], textarea").forEach(function (el) { el.value = ""; });
    }
    renumber();
  });

  if (addBtn && template) {
    addBtn.addEventListener("click", function () {
      var n = parseInt(rows.getAttribute("data-next-index") || "0", 10);
      rows.setAttribute("data-next-index", String(n + 1));
      var wrap = document.createElement("div");
      wrap.innerHTML = template.innerHTML.replace(/__N__/g, String(n)).trim();
      var box = wrap.firstElementChild;
      rows.appendChild(box);
      renumber();
      box.scrollIntoView({ behavior: "smooth", block: "start" });
      var first = box.querySelector("input[type=text]");
      if (first) first.focus({ preventScroll: true });
    });
  }
  renumber();
})();
