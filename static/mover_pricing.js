(function () {
  var RATES = { 2: 180, 3: 235 };
  var manualOverride = false;
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

  function rateForMovers(count) {
    return Object.prototype.hasOwnProperty.call(RATES, count) ? RATES[count] : null;
  }

  function applyRateFromMovers() {
    var moversEl = moversInput();
    var hourlyEl = hourlyInput();
    if (!moversEl || !hourlyEl || manualOverride) {
      return false;
    }
    var count = parseInt(moversEl.value, 10);
    if (isNaN(count)) {
      return false;
    }
    var rate = rateForMovers(count);
    if (rate === null) {
      return false;
    }
    applying = true;
    hourlyEl.value = rate.toFixed(2);
    hourlyEl.dispatchEvent(new Event("input", { bubbles: true }));
    hourlyEl.dispatchEvent(new Event("change", { bubbles: true }));
    applying = false;
    return true;
  }

  function bind() {
    var moversEl = moversInput();
    var hourlyEl = hourlyInput();
    if (!moversEl || !hourlyEl) {
      return;
    }

    moversEl.addEventListener("input", applyRateFromMovers);
    moversEl.addEventListener("change", applyRateFromMovers);

    hourlyEl.addEventListener("input", function () {
      if (!applying) {
        manualOverride = true;
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }

  window.moverPricingApply = applyRateFromMovers;
})();
