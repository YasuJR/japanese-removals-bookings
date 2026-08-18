(function () {
  var panel = document.getElementById("invoice-pricing-panel");
  if (!panel) return;

  var calculateUrl = panel.getAttribute("data-calculate-url");
  var form = panel.closest("form");
  if (!form || !calculateUrl) return;

  var debounceTimer = null;

  function pricingInput(id) {
    return document.getElementById(id);
  }

  function formatDurationValue(hours) {
    var snapped = Math.round(hours / 0.25) * 0.25;
    if (snapped % 1 === 0) {
      return String(snapped);
    }
    return snapped.toFixed(2);
  }

  function syncDurationToFinishTime() {
    var durationEl = pricingInput("pricing_duration_hours");
    var startEl = document.getElementById("start_time");
    var finishEl = document.getElementById("finish_time");
    if (!durationEl || !startEl || !finishEl) return;

    var hours = parseFloat(durationEl.value);
    if (isNaN(hours) || hours <= 0) return;

    var parts = (startEl.value || "08:00").split(":");
    var startMins = parseInt(parts[0], 10) * 60 + parseInt(parts[1] || "0", 10);
    var finishMins = startMins + Math.round(hours * 60);
    var fh = Math.floor(finishMins / 60) % 24;
    var fm = finishMins % 60;
    var finishVal =
      String(fh).padStart(2, "0") + ":" + String(fm).padStart(2, "0");
    finishEl.value = finishVal;
  }

  function syncFinishToDuration() {
    var durationEl = pricingInput("pricing_duration_hours");
    var startEl = document.getElementById("start_time");
    var finishEl = document.getElementById("finish_time");
    if (!durationEl || !startEl || !finishEl || finishEl.type !== "time") return;
    if (!startEl.value || !finishEl.value) return;

    var startParts = startEl.value.split(":");
    var finishParts = finishEl.value.split(":");
    var startMins =
      parseInt(startParts[0], 10) * 60 + parseInt(startParts[1] || "0", 10);
    var finishMins =
      parseInt(finishParts[0], 10) * 60 + parseInt(finishParts[1] || "0", 10);
    if (finishMins <= startMins) return;

    var hours = Math.round(((finishMins - startMins) / 60) * 100) / 100;
    durationEl.value = formatDurationValue(hours);
  }

  function scheduleRecalc() {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(recalculate, 120);
  }

  function setText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function toggleGstRows(enabled) {
    var netRow = document.getElementById("live-net-row");
    var gstRow = document.getElementById("live-gst-row");
    if (netRow) netRow.hidden = !enabled;
    if (gstRow) gstRow.hidden = !enabled;
  }

  function formatAud(amount) {
    var n = parseFloat(amount);
    if (isNaN(n)) return "$0.00";
    return "$" + n.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  function recalculate() {
    syncDurationToFinishTime();
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
        setText("live-labour-total", formatAud(data.labour_total));
        setText("live-extras-total", formatAud(data.extras_total));
        setText("live-net-sales", formatAud(data.net_sales));
        setText("live-gst-amount", formatAud(data.gst_amount));
        setText("live-bank-total", data.bank_total_display);
        setText("live-card-total", data.card_total_display);
        var note = document.getElementById("live-surcharge-note");
        if (note) {
          note.textContent =
            "+" + data.surcharge_percent_display + "% surcharge";
        }
        toggleGstRows(!!data.gst_enabled);
        var descEl = document.getElementById("invoice_description");
        var customEl = document.getElementById("invoice_description_custom");
        if (
          descEl &&
          customEl &&
          customEl.value !== "1" &&
          typeof data.labour_description === "string"
        ) {
          descEl.value = data.labour_description;
        }
      })
      .catch(function () {
        /* keep last displayed totals on transient errors */
      });
  }

  function bindSteppers() {
    panel.querySelectorAll(".stepper-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var target = btn.getAttribute("data-step-target");
        var step = parseFloat(btn.getAttribute("data-step") || "0");
        var input =
          target === "hourly_rate"
            ? pricingInput("pricing_hourly_rate")
            : target === "callout_fee"
              ? pricingInput("pricing_callout_fee")
              : pricingInput("pricing_duration_hours");
        if (!input) return;
        var val = parseFloat(input.value) || 0;
        var next = Math.max(parseFloat(input.min || "0"), val + step);
        if (input.max) next = Math.min(parseFloat(input.max), next);
        input.value =
          target === "duration_hours"
            ? formatDurationValue(next)
            : next.toFixed(2);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
    });
  }

  function bindExtraCharges() {
    var body = document.getElementById("extra-charges-body");
    var addBtn = document.getElementById("extra-charge-add");
    if (!body || !addBtn) return;

    function bindRemoveButtons() {
      body.querySelectorAll(".extra-charge-remove").forEach(function (btn) {
        btn.onclick = function () {
          btn.closest("tr").remove();
          scheduleRecalc();
        };
      });
    }

    function bindInputs(container) {
      container.querySelectorAll("input").forEach(function (inp) {
        inp.addEventListener("input", scheduleRecalc);
        inp.addEventListener("change", scheduleRecalc);
      });
    }

    addBtn.addEventListener("click", function () {
      var row = document.createElement("tr");
      row.className = "extra-charge-row";
      row.innerHTML =
        '<td><input type="text" name="extra_description" placeholder="e.g. Piano Fee"></td>' +
        '<td><input type="number" name="extra_quantity" min="0.01" step="0.01" value="1"></td>' +
        '<td><input type="number" name="extra_unit_price" min="0" step="0.01" value="0"></td>' +
        '<td><button type="button" class="btn-secondary btn-sm extra-charge-remove">Remove</button></td>';
      body.appendChild(row);
      bindRemoveButtons();
      bindInputs(row);
      scheduleRecalc();
    });

    bindInputs(body);
    bindRemoveButtons();
  }

  panel.querySelectorAll("input, select, textarea").forEach(function (el) {
    el.addEventListener("input", scheduleRecalc);
    el.addEventListener("change", scheduleRecalc);
  });

  var startEl = document.getElementById("start_time");
  if (startEl) {
    startEl.addEventListener("input", scheduleRecalc);
    startEl.addEventListener("change", scheduleRecalc);
  }

  var finishEl = document.getElementById("finish_time");
  if (finishEl && finishEl.type === "time") {
    finishEl.addEventListener("input", function () {
      syncFinishToDuration();
      scheduleRecalc();
    });
    finishEl.addEventListener("change", function () {
      syncFinishToDuration();
      scheduleRecalc();
    });
  }

  bindSteppers();
  bindExtraCharges();

  var invoiceDescEl = document.getElementById("invoice_description");
  var invoiceDescCustomEl = document.getElementById("invoice_description_custom");
  if (invoiceDescEl && invoiceDescCustomEl) {
    invoiceDescEl.addEventListener("input", function () {
      invoiceDescCustomEl.value = "1";
    });
  }

  scheduleRecalc();
})();
