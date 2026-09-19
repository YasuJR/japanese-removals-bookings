(function () {
  function hourlyInput() {
    return (
      document.getElementById("pricing_hourly_rate") ||
      document.getElementById("hourly_rate") ||
      document.querySelector('input[name="hourly_rate"]')
    );
  }

  function minutesInput() {
    return (
      document.getElementById("callout_minutes") ||
      document.querySelector('select[name="callout_minutes"]')
    );
  }

  function feeInput() {
    return (
      document.querySelector('input[name="callout_fee"].callout-fee-auto') ||
      document.getElementById("pricing_callout_fee") ||
      document.getElementById("callout_fee") ||
      document.querySelector('input[name="callout_fee"]')
    );
  }

  function overrideInput() {
    return document.getElementById("callout_fee_override");
  }

  function computedFee(rate, minutes) {
    var r = parseFloat(rate);
    var m = parseInt(minutes, 10);
    if (isNaN(r) || isNaN(m) || r <= 0 || m <= 0) {
      return 0;
    }
    return Math.round(r * (m / 60) * 100) / 100;
  }

  function syncCalloutFee() {
    var hourlyEl = hourlyInput();
    var minutesEl = minutesInput();
    var feeEl = feeInput();
    if (!hourlyEl || !minutesEl || !feeEl) {
      return;
    }
    var overrideEl = overrideInput();
    if (overrideEl && String(overrideEl.value || "").trim() !== "") {
      return;
    }
    var fee = computedFee(hourlyEl.value, minutesEl.value);
    feeEl.value = fee.toFixed(2);
    feeEl.dispatchEvent(new Event("input", { bubbles: true }));
    feeEl.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function bind() {
    var hourlyEl = hourlyInput();
    var minutesEl = minutesInput();
    if (!hourlyEl || !minutesEl) {
      return;
    }
    hourlyEl.addEventListener("input", syncCalloutFee);
    hourlyEl.addEventListener("change", syncCalloutFee);
    minutesEl.addEventListener("input", syncCalloutFee);
    minutesEl.addEventListener("change", syncCalloutFee);
    var overrideEl = overrideInput();
    if (overrideEl) {
      overrideEl.addEventListener("input", function () {
        if (String(overrideEl.value || "").trim() === "") {
          syncCalloutFee();
        }
      });
    }
    syncCalloutFee();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }

  window.calloutPricingSync = syncCalloutFee;
})();
