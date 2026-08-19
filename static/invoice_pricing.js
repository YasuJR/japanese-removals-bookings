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

  function finishTimeIsUserEditable() {
    var finishEl = document.getElementById("finish_time");
    return !!(finishEl && finishEl.type === "time");
  }

  // Edit Booking: Start/Finish are the source of truth.
  // New Booking: Duration drives the hidden Finish field.
  var lastTimeEditSource = finishTimeIsUserEditable() ? "times" : "duration";

  function parseTimeMinutes(value) {
    var parts = String(value || "").split(":");
    var hours = parseInt(parts[0], 10);
    var minutes = parseInt(parts[1] || "0", 10);
    if (isNaN(hours) || isNaN(minutes)) return null;
    return hours * 60 + minutes;
  }

  function formatHm(totalMins) {
    var mins = ((totalMins % (24 * 60)) + 24 * 60) % (24 * 60);
    var fh = Math.floor(mins / 60);
    var fm = mins % 60;
    return String(fh).padStart(2, "0") + ":" + String(fm).padStart(2, "0");
  }

  function formatDurationValue(hours) {
    var snapped = Math.round(hours / 0.25) * 0.25;
    if (snapped % 1 === 0) {
      return String(snapped);
    }
    return snapped.toFixed(2);
  }

  function formatExactDuration(hours) {
    var rounded = Math.round(hours * 100) / 100;
    if (rounded % 1 === 0) {
      return String(rounded);
    }
    return String(rounded);
  }

  function syncDurationToFinishTime() {
    var durationEl = pricingInput("pricing_duration_hours");
    var startEl = document.getElementById("start_time");
    var finishEl = document.getElementById("finish_time");
    if (!durationEl || !startEl || !finishEl) return;

    var hours = parseFloat(durationEl.value);
    if (isNaN(hours) || hours <= 0) return;

    var startMins = parseTimeMinutes(startEl.value || "08:00");
    if (startMins == null) return;
    finishEl.value = formatHm(startMins + Math.round(hours * 60));
  }

  function syncFinishToDuration() {
    var durationEl = pricingInput("pricing_duration_hours");
    var startEl = document.getElementById("start_time");
    var finishEl = document.getElementById("finish_time");
    if (!durationEl || !startEl || !finishEl || finishEl.type !== "time") return;
    if (!startEl.value || !finishEl.value) return;

    var startMins = parseTimeMinutes(startEl.value);
    var finishMins = parseTimeMinutes(finishEl.value);
    if (startMins == null || finishMins == null) return;
    if (finishMins <= startMins) return;

    durationEl.value = formatExactDuration((finishMins - startMins) / 60);
  }

  function startChange() {
    if (finishTimeIsUserEditable()) {
      lastTimeEditSource = "times";
      syncFinishToDuration();
    }
    scheduleRecalc();
  }

  function finishChange() {
    lastTimeEditSource = "times";
    syncFinishToDuration();
    scheduleRecalc();
  }

  function durationChange() {
    lastTimeEditSource = "duration";
    syncDurationToFinishTime();
    scheduleRecalc();
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
    var abs = Math.abs(n)
      .toFixed(2)
      .replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    return (n < 0 ? "-$" : "$") + abs;
  }

  function recalculate() {
    if (lastTimeEditSource === "duration") {
      syncDurationToFinishTime();
    } else {
      syncFinishToDuration();
    }
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

    function extraChargeRowHtml(description, quantity, unitPrice) {
      var desc = String(description || "")
        .replace(/&/g, "&amp;")
        .replace(/"/g, "&quot;")
        .replace(/</g, "&lt;");
      return (
        '<td><input type="text" name="extra_description" placeholder="e.g. Piano Fee" value="' +
        desc +
        '"></td>' +
        '<td><input type="number" name="extra_quantity" min="0.01" step="0.01" value="' +
        (quantity || "1") +
        '"></td>' +
        '<td><input type="number" name="extra_unit_price" step="0.01" value="' +
        (unitPrice || "0") +
        '"></td>' +
        '<td><button type="button" class="btn-secondary btn-sm extra-charge-remove">Remove</button></td>'
      );
    }

    function addChargeRow(description, quantity, unitPrice, focusUnit) {
      var row = document.createElement("tr");
      row.className = "extra-charge-row";
      row.innerHTML = extraChargeRowHtml(description, quantity, unitPrice);
      body.appendChild(row);
      bindRemoveButtons();
      bindInputs(row);
      if (focusUnit) {
        var unitEl = row.querySelector('input[name="extra_unit_price"]');
        if (unitEl) unitEl.focus();
      }
      scheduleRecalc();
      return row;
    }

    addBtn.addEventListener("click", function () {
      addChargeRow("", "1", "0", false);
    });

    bindInputs(body);
    bindRemoveButtons();

    var breakBtn = document.getElementById("extra-adjust-break");
    if (breakBtn) {
      breakBtn.addEventListener("click", function () {
        var rateEl = pricingInput("pricing_hourly_rate");
        var minutesEl = document.getElementById("extra-break-minutes");
        var rate = parseFloat(rateEl && rateEl.value ? rateEl.value : "0");
        var minutes = parseFloat(minutesEl && minutesEl.value ? minutesEl.value : "30");
        if (isNaN(minutes) || minutes <= 0) minutes = 30;
        var unit = 0;
        if (!isNaN(rate) && rate > 0) {
          unit = Math.round(rate * (minutes / 60) * -1 * 100) / 100;
        }
        var minuteLabel = Number.isInteger(minutes) ? String(minutes) : String(minutes);
        addChargeRow(minuteLabel + " min break deduction", "1", String(unit.toFixed(2)), unit === 0);
      });
    }

    panel.querySelectorAll(".extra-adjust-preset").forEach(function (btn) {
      btn.addEventListener("click", function () {
        addChargeRow(btn.getAttribute("data-description") || "Adjustment", "1", "0", true);
      });
    });
  }

  panel.querySelectorAll("input, select, textarea").forEach(function (el) {
    el.addEventListener("input", scheduleRecalc);
    el.addEventListener("change", scheduleRecalc);
  });

  var startEl = document.getElementById("start_time");
  if (startEl) {
    startEl.addEventListener("input", startChange);
    startEl.addEventListener("change", startChange);
  }

  var finishEl = document.getElementById("finish_time");
  if (finishEl && finishEl.type === "time") {
    finishEl.addEventListener("input", finishChange);
    finishEl.addEventListener("change", finishChange);
  }

  var durationEl = pricingInput("pricing_duration_hours");
  if (durationEl) {
    durationEl.addEventListener("input", durationChange);
    durationEl.addEventListener("change", durationChange);
  }

  form.addEventListener("submit", function () {
    [startEl, finishEl].forEach(function (el) {
      if (!el || !el.value) return;
      var mins = parseTimeMinutes(el.value);
      if (mins != null) el.value = formatHm(mins);
    });
    if (lastTimeEditSource === "duration") {
      syncDurationToFinishTime();
    } else if (finishTimeIsUserEditable()) {
      syncFinishToDuration();
    }
  });

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
