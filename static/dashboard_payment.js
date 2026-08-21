(function () {
  var pickers = document.querySelectorAll(".dashboard-payment-picker");
  var portal = document.getElementById("dashboard-payment-portal");
  if (!pickers.length || !portal) return;

  var menu = portal.querySelector(".dashboard-payment-menu");
  var backdrop = portal.querySelector(".dashboard-payment-backdrop");
  var optionsJson = document.getElementById("dashboard-payment-options-json");
  if (!menu || !backdrop || !optionsJson) return;

  var paymentOptions = [];
  try {
    paymentOptions = JSON.parse(optionsJson.textContent || "[]");
  } catch (err) {
    return;
  }

  var openPicker = null;

  function paymentClasses() {
    return [
      "status-payment-paid",
      "status-payment-unpaid",
      "status-payment-part-paid",
      "status-payment-overdue",
    ];
  }

  function buildMenu(currentPayment) {
    menu.innerHTML = "";
    paymentOptions.forEach(function (option) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className =
        "dashboard-payment-option status-pill status-payment-" +
        option.toLowerCase().replace(/\s+/g, "-");
      btn.setAttribute("data-payment-status", option);
      btn.setAttribute("role", "option");
      btn.setAttribute(
        "aria-selected",
        option === currentPayment ? "true" : "false"
      );
      btn.textContent = option;
      menu.appendChild(btn);
    });
  }

  function closePortal() {
    portal.hidden = true;
    portal.setAttribute("aria-hidden", "true");
    menu.style.top = "";
    menu.style.left = "";
    menu.style.minWidth = "";
    if (openPicker) {
      var trigger = openPicker.querySelector(".dashboard-payment-trigger");
      if (trigger) trigger.setAttribute("aria-expanded", "false");
    }
    openPicker = null;
  }

  window.dashboardPaymentClose = closePortal;

  function positionMenu(trigger) {
    var rect = trigger.getBoundingClientRect();
    var top = rect.bottom + 4;
    var left = rect.left;
    var minWidth = Math.max(rect.width, 148);

    menu.style.top = top + "px";
    menu.style.left = left + "px";
    menu.style.minWidth = minWidth + "px";

    var menuRect = menu.getBoundingClientRect();
    if (menuRect.bottom > window.innerHeight - 8) {
      top = Math.max(8, rect.top - menuRect.height - 4);
      menu.style.top = top + "px";
    }
    if (menuRect.right > window.innerWidth - 8) {
      left = Math.max(8, window.innerWidth - menuRect.width - 8);
      menu.style.left = left + "px";
    }
  }

  function openPortal(picker, trigger) {
    if (window.dashboardStatusClose) window.dashboardStatusClose();
    var currentPayment =
      picker.getAttribute("data-current-payment") ||
      picker.querySelector(".dashboard-payment-label").textContent;
    buildMenu(currentPayment);
    openPicker = picker;
    portal.hidden = false;
    portal.setAttribute("aria-hidden", "false");
    trigger.setAttribute("aria-expanded", "true");
    positionMenu(trigger);
  }

  function applyPayment(picker, paymentStatus, cssClass) {
    var trigger = picker.querySelector(".dashboard-payment-trigger");
    var label = picker.querySelector(".dashboard-payment-label");
    if (!trigger || !label) return;
    paymentClasses().forEach(function (cls) {
      trigger.classList.remove(cls);
    });
    trigger.classList.add("status-payment-" + cssClass);
    label.textContent = paymentStatus;
    picker.setAttribute("data-current-payment", paymentStatus);
  }

  function savePayment(picker, paymentStatus) {
    var bookingId = picker.getAttribute("data-booking-id");
    var trigger = picker.querySelector(".dashboard-payment-trigger");
    if (!bookingId || !trigger) return;

    trigger.disabled = true;
    picker.classList.add("is-saving");

    fetch("/bookings/" + bookingId + "/payment", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify({ payment_status: paymentStatus }),
    })
      .then(function (res) {
        return res.json().then(function (data) {
          if (!res.ok) throw new Error(data.error || "Could not update payment");
          return data;
        });
      })
      .then(function (data) {
        applyPayment(picker, data.payment_status, data.css_class);
        if (data.job_status && window.dashboardApplyStatus) {
          var statusPicker = document.querySelector(
            '.dashboard-status-picker[data-booking-id="' +
              picker.getAttribute("data-booking-id") +
              '"]'
          );
          if (statusPicker) {
            window.dashboardApplyStatus(
              statusPicker,
              data.job_status,
              data.job_status_css || "completed"
            );
          }
        }
        closePortal();
      })
      .catch(function (err) {
        window.alert(err.message || "Could not update payment.");
      })
      .finally(function () {
        trigger.disabled = false;
        picker.classList.remove("is-saving");
      });
  }

  pickers.forEach(function (picker) {
    var trigger = picker.querySelector(".dashboard-payment-trigger");
    if (!trigger) return;

    trigger.addEventListener("click", function (event) {
      event.preventDefault();
      event.stopPropagation();
      if (openPicker === picker && !portal.hidden) {
        closePortal();
        return;
      }
      closePortal();
      openPortal(picker, trigger);
    });
  });

  menu.addEventListener("click", function (event) {
    var option = event.target.closest(".dashboard-payment-option");
    if (!option || !openPicker) return;
    event.stopPropagation();
    var nextStatus = option.getAttribute("data-payment-status");
    if (!nextStatus) return;
    savePayment(openPicker, nextStatus);
  });

  backdrop.addEventListener("click", function (event) {
    event.stopPropagation();
    closePortal();
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closePortal();
  });
  window.addEventListener("scroll", closePortal, true);
  window.addEventListener("resize", closePortal);
})();
