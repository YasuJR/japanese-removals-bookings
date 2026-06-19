(function () {
  var toggle = document.querySelector(".nav-toggle");
  var nav = document.getElementById("main-nav");
  if (toggle && nav) {
    toggle.addEventListener("click", function () {
      var open = nav.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  var startEl = document.getElementById("start_time");
  var durationEl = document.getElementById("duration_hours");
  var finishEl = document.getElementById("finish_time");
  var finishText = document.getElementById("finish_live_text");
  if (!startEl || !durationEl || !finishEl) {
    return;
  }

  function pad2(n) {
    return n < 10 ? "0" + n : String(n);
  }

  function format12h(hours, minutes) {
    var h = hours % 12 || 12;
    var suffix = hours < 12 ? "AM" : "PM";
    return h + ":" + pad2(minutes) + " " + suffix;
  }

  function computeFinish() {
    var startVal = startEl.value;
    var durationVal = parseFloat(durationEl.value, 10);
    if (!startVal || isNaN(durationVal) || durationVal <= 0) {
      if (finishText) {
        finishText.textContent = "—";
      }
      return;
    }
    var parts = startVal.split(":");
    var hours = parseInt(parts[0], 10);
    var minutes = parseInt(parts[1], 10);
    var totalMinutes = hours * 60 + minutes + Math.round(durationVal * 60);
    if (totalMinutes >= 24 * 60) {
      finishEl.value = "23:59";
      if (finishText) {
        finishText.textContent = format12h(23, 59);
      }
      return;
    }
    var endH = Math.floor(totalMinutes / 60);
    var endM = totalMinutes % 60;
    finishEl.value = pad2(endH) + ":" + pad2(endM);
    if (finishText) {
      finishText.textContent = format12h(endH, endM);
    }
  }

  startEl.addEventListener("change", computeFinish);
  startEl.addEventListener("input", computeFinish);
  durationEl.addEventListener("change", computeFinish);
  durationEl.addEventListener("input", computeFinish);
  computeFinish();
})();
