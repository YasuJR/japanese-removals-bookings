(function () {
  var HOURLY_RATES = { 2: 180, 3: 235 };
  var CALLOUT_FEES = { 2: 90, 3: 117.5 };
  var manualHourlyOverride = false;
  var manualCalloutOverride = false;
  var applying = false;

  function moversInput() {
    return document.querySelector('input[name="num_movers"]');
  }

  function hourlyInput() {
    return (
      document.getElementById("pricing_hourly_rate") ||
      document.getElementById("hourly_rate") ||
      document.querySelector('input[name="hourly_rate"]')
    );
  }

  function calloutInput() {
    return (
      document.getElementById("pricing_callout_fee") ||
      document.getElementById("callout_fee") ||
      document.querySelector('input[name="callout_fee"]')
    );
  }

  function dispatchFieldEvents(el) {
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function applyPricingFromMovers() {
    var moversEl = moversInput();
    if (!moversEl) {
      return false;
    }
    var count = parseInt(moversEl.value, 10);
    if (isNaN(count)) {
      return false;
    }
    var hourlyEl = hourlyInput();
    var calloutEl = calloutInput();
    var hourlyRate = Object.prototype.hasOwnProperty.call(HOURLY_RATES, count)
      ? HOURLY_RATES[count]
      : null;
    var calloutFee = Object.prototype.hasOwnProperty.call(CALLOUT_FEES, count)
      ? CALLOUT_FEES[count]
      : null;
    if (hourlyRate === null && calloutFee === null) {
      return false;
    }

    applying = true;
    var changed = false;
    if (!manualHourlyOverride && hourlyEl && hourlyRate !== null) {
      hourlyEl.value = hourlyRate.toFixed(2);
      dispatchFieldEvents(hourlyEl);
      changed = true;
    }
    if (!manualCalloutOverride && calloutEl && calloutFee !== null) {
      calloutEl.value = calloutFee.toFixed(2);
      dispatchFieldEvents(calloutEl);
      changed = true;
    }
    applying = false;
    return changed;
  }

  function bind() {
    var moversEl = moversInput();
    var hourlyEl = hourlyInput();
    var calloutEl = calloutInput();
    if (!moversEl || (!hourlyEl && !calloutEl)) {
      return;
    }

    moversEl.addEventListener("input", applyPricingFromMovers);
    moversEl.addEventListener("change", applyPricingFromMovers);

    if (hourlyEl) {
      hourlyEl.addEventListener("input", function () {
        if (!applying) {
          manualHourlyOverride = true;
        }
      });
    }

    if (calloutEl) {
      calloutEl.addEventListener("input", function () {
        if (!applying) {
          manualCalloutOverride = true;
        }
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }

  window.moverPricingApply = applyPricingFromMovers;
})();
