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

  function parseTimeMinutes(value) {
    var parts = String(value || "").split(":");
    var hours = parseInt(parts[0], 10);
    var minutes = parseInt(parts[1] || "0", 10);
    if (isNaN(hours) || isNaN(minutes)) return null;
    return hours * 60 + minutes;
  }

  function durationHours() {
    var startEl = document.getElementById("start_time");
    var finishEl = document.getElementById("finish_time");
    var startMins = parseTimeMinutes(startEl && startEl.value);
    var finishMins = parseTimeMinutes(finishEl && finishEl.value);
    if (startMins != null && finishMins != null && finishMins > startMins) {
      return Math.round(((finishMins - startMins) / 60) * 100) / 100;
    }
    var durationEl =
      document.getElementById("pricing_duration_hours") ||
      document.getElementById("duration_hours");
    var hours = parseFloat(durationEl && durationEl.value);
    if (!Number.isFinite(hours) || hours <= 0) return null;
    return Math.round(hours * 100) / 100;
  }

  function bindPanel(panel) {
    var inputs = panel.querySelectorAll(".job-cost-input");
    var display = panel.querySelector("#job-cost-total-display");
    var staffInput = panel.querySelector('input[name="staff_cost"]');
    var fuelInput = panel.querySelector('input[name="fuel_cost"]');
    var manualEl = panel.querySelector("#staff_cost_manual");
    if (!inputs.length || !display) return;

    var staffRate = Number(panel.getAttribute("data-staff-rate") || "72");
    var defaultFuel = Number(panel.getAttribute("data-default-fuel") || "30");
    var isNew = panel.getAttribute("data-new-booking") === "1";
    var applyingStaff = false;
    var staffManual = !!(manualEl && manualEl.value === "1");

    function updateTotal() {
      var total = 0;
      inputs.forEach(function (input) {
        total += parseAmount(input.value);
      });
      display.textContent = formatAud(Math.round(total * 100) / 100);
    }

    function defaultStaffForDuration() {
      var hours = durationHours();
      if (hours == null) return null;
      return Math.round(staffRate * hours * 100) / 100;
    }

    function setManual(flag) {
      staffManual = !!flag;
      if (manualEl) manualEl.value = staffManual ? "1" : "0";
    }

    function applyStaffFromDuration() {
      if (staffManual || !staffInput) return;
      var amount = defaultStaffForDuration();
      if (amount == null) return;
      applyingStaff = true;
      staffInput.value = amount.toFixed(2);
      applyingStaff = false;
      updateTotal();
    }

    if (isNew && fuelInput && !String(fuelInput.value || "").trim()) {
      fuelInput.value = defaultFuel.toFixed(2);
    }

    if (isNew) {
      applyStaffFromDuration();
    } else if (staffInput) {
      var expected = defaultStaffForDuration();
      var current = String(staffInput.value || "").trim();
      if (
        expected != null &&
        current &&
        Math.abs(parseAmount(current) - expected) < 0.005
      ) {
        setManual(false);
      } else if (current) {
        setManual(true);
      }
    }

    if (staffInput) {
      staffInput.addEventListener("input", function () {
        if (!applyingStaff) setManual(true);
        updateTotal();
      });
      staffInput.addEventListener("change", function () {
        if (!applyingStaff) setManual(true);
        updateTotal();
      });
    }

    inputs.forEach(function (input) {
      if (input === staffInput) return;
      input.addEventListener("input", updateTotal);
      input.addEventListener("change", updateTotal);
    });

    function onDurationRelatedChange() {
      setTimeout(applyStaffFromDuration, 0);
    }

    ["start_time", "finish_time", "duration_hours", "pricing_duration_hours"].forEach(
      function (id) {
        var el = document.getElementById(id);
        if (!el) return;
        el.addEventListener("input", onDurationRelatedChange);
        el.addEventListener("change", onDurationRelatedChange);
      }
    );

    updateTotal();
  }

  document.querySelectorAll("#job-costs-panel").forEach(bindPanel);
})();
