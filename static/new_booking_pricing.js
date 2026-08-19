(function () {
  var panel = document.getElementById("new-booking-invoice-panel");
  if (!panel) return;

  var calculateUrl = panel.getAttribute("data-calculate-url");
  var form = panel.closest("form");
  if (!form || !calculateUrl) return;

  var debounceTimer = null;

  function setText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function toggleGstRows(enabled) {
    var netRow = document.getElementById("new-live-net-row");
    var gstRow = document.getElementById("new-live-gst-row");
    if (netRow) netRow.hidden = !enabled;
    if (gstRow) gstRow.hidden = !enabled;
  }

  function formatAud(amount) {
    var n = parseFloat(amount);
    if (isNaN(n)) return "$0.00";
    var abs = Math.abs(n)
      .toFixed(2)
      .replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    return (n < 0 ? "-$" : "$") + abs;
  }

  function scheduleRecalc() {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(recalculate, 120);
  }

  function recalculate() {
    var body = new FormData(form);
    fetch(calculateUrl, {
      method: "POST",
      body: body,
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (res) {
        return res.json().then(function (data) {
          if (!res.ok) throw new Error(data.error || "Calculation failed");
          return data;
        });
      })
      .then(function (data) {
        setText(
          "new-live-rate-summary",
          formatAud(data.hourly_rate) +
            "/hr × " +
            data.hours +
            "h" +
            (data.gst_enabled ? " (incl. GST)" : "")
        );
        setText(
          "new-live-callout-summary",
          formatAud(data.callout_fee) + (data.gst_enabled ? " (incl. GST)" : "")
        );
        setText("new-live-total-summary", formatAud(data.total));
        setText("new-live-net-sales", formatAud(data.net_sales));
        setText("new-live-gst-amount", formatAud(data.gst_amount));
        toggleGstRows(!!data.gst_enabled);
      })
      .catch(function () {
        /* keep last displayed totals on transient errors */
      });
  }

  form.querySelectorAll("input, select, textarea").forEach(function (el) {
    el.addEventListener("input", scheduleRecalc);
    el.addEventListener("change", scheduleRecalc);
  });

  scheduleRecalc();
})();
