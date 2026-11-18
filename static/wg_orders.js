// WG Orders page: "+ Add WG Order" adds another order for the same day; "Remove this order" drops one.
(function () {
  var rows = document.getElementById("wg-order-rows");
  var template = document.getElementById("wg-order-template");
  var addBtn = document.getElementById("wg-order-add");
  if (!rows) return;

  function renumber() {
    var labels = rows.querySelectorAll(".wg-order-number");
    for (var i = 0; i < labels.length; i++) labels[i].textContent = String(i + 1);
  }

  rows.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-wg-remove]");
    if (!btn) return;
    var box = btn.closest("[data-wg-order]");
    if (box && rows.querySelectorAll("[data-wg-order]").length > 1) {
      box.remove();
      renumber();
    } else if (box) {
      box.querySelectorAll("input[type=text], input[type=tel], input[type=time], textarea").forEach(function (el) { el.value = ""; });
    }
  });

  if (addBtn && template) {
    addBtn.addEventListener("click", function () {
      var n = parseInt(rows.getAttribute("data-next-index") || "0", 10);
      rows.setAttribute("data-next-index", String(n + 1));
      var html = template.innerHTML.replace(/__N__/g, String(n));
      var wrap = document.createElement("div");
      wrap.innerHTML = html.trim();
      var box = wrap.firstElementChild;
      // Same day as the previous order unless the user changes it.
      var dates = rows.querySelectorAll("[data-wg-date]");
      var last = dates.length ? dates[dates.length - 1].value : "";
      var dateEl = box.querySelector("[data-wg-date]");
      if (dateEl && last) dateEl.value = last;
      rows.appendChild(box);
      renumber();
      var first = box.querySelector("input[type=text]");
      if (first) first.focus();
    });
  }
  renumber();
})();
