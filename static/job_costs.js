(function () {
  function parseAmount(value) {
    var text = String(value || "").trim();
    if (!text) return 0;
    var amount = Number(text);
    if (!Number.isFinite(amount) || amount < 0) return 0;
    return Math.round(amount * 100) / 100;
  }

  function formatAud(amount) {
    var value = Math.round(Number(amount) * 100) / 100;
    var abs = Math.abs(value).toFixed(2);
    var parts = abs.split(".");
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    var formatted = "$" + parts.join(".");
    return value < 0 ? "-" + formatted : formatted;
  }

  function bindPanel(panel) {
    var inputs = panel.querySelectorAll(".job-cost-input");
    var display = panel.querySelector("#job-cost-total-display");
    if (!inputs.length || !display) return;

    function updateTotal() {
      var total = 0;
      inputs.forEach(function (input) {
        total += parseAmount(input.value);
      });
      display.textContent = formatAud(Math.round(total * 100) / 100);
    }

    inputs.forEach(function (input) {
      input.addEventListener("input", updateTotal);
      input.addEventListener("change", updateTotal);
    });
    updateTotal();
  }

  document.querySelectorAll("#job-costs-panel").forEach(bindPanel);
})();
