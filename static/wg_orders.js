// WG Orders page: add as many order cards as needed, remove one, copy the previous
// card's crew & truck. "+ Add WG Order" copies only the previous card's Date.
(function () {
  var rows = document.getElementById("wg-order-rows");
  var template = document.getElementById("wg-order-template");
  var addBtn = document.getElementById("wg-order-add");
  var countEl = document.getElementById("wg-order-count");
  if (!rows) return;

  function cards() {
    return rows.querySelectorAll("[data-wg-order]");
  }

  function renumber() {
    var labels = rows.querySelectorAll(".wg-order-number");
    for (var i = 0; i < labels.length; i++) labels[i].textContent = String(i + 1);
    var n = cards().length;
    if (countEl) countEl.textContent = n + (n === 1 ? " order" : " orders");
  }

  function hasContent(box) {
    var fields = box.querySelectorAll("input[type=text], input[type=tel], input[type=time], textarea");
    for (var i = 0; i < fields.length; i++) {
      if (fields[i].value.trim() && fields[i].name.indexOf("payment_type") === -1) return true;
    }
    return false;
  }

  function copyPrevious(box) {
    var all = Array.prototype.slice.call(cards());
    var index = all.indexOf(box);
    if (index < 1) return;
    var prev = all[index - 1];
    var prevChecked = {};
    prev.querySelectorAll("[data-wg-crew] input[type=checkbox]").forEach(function (cb) {
      prevChecked[cb.value] = cb.checked;
    });
    box.querySelectorAll("[data-wg-crew] input[type=checkbox]").forEach(function (cb) {
      cb.checked = !!prevChecked[cb.value];
    });
    var prevTruck = prev.querySelector("[data-wg-truck]");
    var truck = box.querySelector("[data-wg-truck]");
    if (prevTruck && truck) truck.value = prevTruck.value;
  }

  rows.addEventListener("click", function (event) {
    var copyBtn = event.target.closest("[data-wg-copy-prev]");
    if (copyBtn) {
      copyPrevious(copyBtn.closest("[data-wg-order]"));
      return;
    }
    var btn = event.target.closest("[data-wg-remove]");
    if (!btn) return;
    var box = btn.closest("[data-wg-order]");
    if (!box) return;
    if (hasContent(box) && !window.confirm("Remove this WG order? It hasn't been saved.")) return;
    if (cards().length > 1) {
      box.remove();
    } else {
      box.querySelectorAll("input[type=text], input[type=tel], input[type=time], textarea").forEach(function (el) { el.value = ""; });
      box.querySelectorAll("input[type=checkbox]").forEach(function (el) { el.checked = false; });
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
      // Same day as the previous order; crew / truck only via "Copy previous".
      var dates = rows.querySelectorAll("[data-wg-date]");
      var last = dates.length ? dates[dates.length - 1].value : "";
      var dateEl = box.querySelector("[data-wg-date]");
      if (dateEl && last) dateEl.value = last;
      rows.appendChild(box);
      renumber();
      box.scrollIntoView({ behavior: "smooth", block: "start" });
      var first = box.querySelector("input[type=text]");
      if (first) first.focus({ preventScroll: true });
    });
  }
  renumber();
})();
