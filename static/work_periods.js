// Booking Edit: optional second work period (when the number of movers changed mid-job).
(function () {
  var flag = document.getElementById("work_period_2_enabled");
  var box = document.getElementById("work-period-2");
  var addBtn = document.getElementById("work-period-2-add");
  var removeBtn = document.getElementById("work-period-2-remove");
  if (!flag || !box || !addBtn || !removeBtn) return;

  var startEl = document.getElementById("work_period_2_start");
  var finishEl = document.getElementById("work_period_2_finish");
  var moversEl = document.getElementById("work_period_2_movers");
  var rateEl = document.getElementById("work_period_2_rate");
  var breakWrap = document.getElementById("extra-break-period-wrap");
  var manualRate = !!(rateEl && rateEl.value);
  var applying = false;

  function recalc() {
    if (window.invoicePricingRecalc) window.invoicePricingRecalc();
  }

  function applyRate() {
    if (!moversEl || !rateEl || manualRate || !window.moverHourlyRateFor) return;
    var rate = window.moverHourlyRateFor(moversEl.value);
    if (rate === null) return;
    applying = true;
    rateEl.value = Number(rate).toFixed(2);
    applying = false;
  }

  function setOpen(open) {
    flag.value = open ? "1" : "0";
    box.hidden = !open;
    addBtn.hidden = open;
    if (breakWrap) breakWrap.hidden = !open;
    if (open) {
      var p1Finish = document.getElementById("finish_time");
      if (startEl && !startEl.value && p1Finish && p1Finish.value) startEl.value = p1Finish.value;
      if (startEl) startEl.focus();
    } else {
      [startEl, finishEl, moversEl, rateEl].forEach(function (el) {
        if (el) el.value = "";
      });
      manualRate = false;
    }
    recalc();
  }

  addBtn.addEventListener("click", function () { setOpen(true); });
  removeBtn.addEventListener("click", function () { setOpen(false); });

  if (moversEl) {
    ["input", "change"].forEach(function (evt) {
      moversEl.addEventListener(evt, function () { applyRate(); recalc(); });
    });
  }
  if (rateEl) {
    rateEl.addEventListener("input", function () {
      if (!applying) manualRate = rateEl.value !== "";
      recalc();
    });
  }
  [startEl, finishEl].forEach(function (el) {
    if (!el) return;
    el.addEventListener("input", recalc);
    el.addEventListener("change", recalc);
  });
})();
