(function () {
  var HOURLY_RATES = { 2: 180, 3: 235 };
  var CALLOUT_MINUTES = { 2: 30, 3: 30 };
  var manualHourlyOverride = false;
  var manualCalloutMinutesOverride = false;
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

  function calloutMinutesInput() {
    return (
      document.getElementById("callout_minutes") ||
      document.querySelector('select[name="callout_minutes"]')
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
    var minutesEl = calloutMinutesInput();
    var hourlyRate = Object.prototype.hasOwnProperty.call(HOURLY_RATES, count)
      ? HOURLY_RATES[count]
      : null;
    var calloutMinutes = Object.prototype.hasOwnProperty.call(CALLOUT_MINUTES, count)
      ? CALLOUT_MINUTES[count]
      : null;
    if (hourlyRate === null && calloutMinutes === null) {
      return false;
    }

    applying = true;
    var changed = false;
    if (!manualHourlyOverride && hourlyEl && hourlyRate !== null) {
      hourlyEl.value = hourlyRate.toFixed(2);
      dispatchFieldEvents(hourlyEl);
      changed = true;
    }
    if (!manualCalloutMinutesOverride && minutesEl && calloutMinutes !== null) {
      minutesEl.value = String(calloutMinutes);
      dispatchFieldEvents(minutesEl);
      changed = true;
    }
    applying = false;
    if (changed && window.calloutPricingSync) {
      window.calloutPricingSync();
    }
    return changed;
  }

  function bind() {
    var moversEl = moversInput();
    var hourlyEl = hourlyInput();
    var minutesEl = calloutMinutesInput();
    if (!moversEl || (!hourlyEl && !minutesEl)) {
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

    if (minutesEl) {
      minutesEl.addEventListener("input", function () {
        if (!applying) {
          manualCalloutMinutesOverride = true;
        }
      });
      minutesEl.addEventListener("change", function () {
        if (!applying) {
          manualCalloutMinutesOverride = true;
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
