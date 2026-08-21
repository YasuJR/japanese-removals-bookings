(function () {
  var pickers = document.querySelectorAll(".dashboard-status-picker");
  var portal = document.getElementById("dashboard-status-portal");
  if (!pickers.length || !portal) return;

  var menu = portal.querySelector(".dashboard-status-menu");
  var backdrop = portal.querySelector(".dashboard-status-backdrop");
  var optionsJson = document.getElementById("dashboard-status-options-json");
  if (!menu || !backdrop || !optionsJson) return;

  var statusOptions = [];
  try {
    statusOptions = JSON.parse(optionsJson.textContent || "[]");
  } catch (err) {
    return;
  }

  var openPicker = null;
  var cssClassMap = {
    Quote: "quote",
    Confirmed: "confirmed",
    Invoiced: "invoiced",
    Completed: "completed",
    Cancelled: "cancelled",
    Pending: "pending",
    "On Route": "on-route",
    "In Progress": "in-progress",
    Paid: "paid",
  };

  function statusClasses() {
    return [
      "status-pending",
      "status-quote",
      "status-confirmed",
      "status-on-route",
      "status-in-progress",
      "status-completed",
      "status-invoiced",
      "status-paid",
      "status-cancelled",
    ];
  }

  function buildMenu(currentStatus) {
    menu.innerHTML = "";
    statusOptions.forEach(function (option) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className =
        "dashboard-status-option status-badge status-" +
        (cssClassMap[option] || "quote");
      btn.setAttribute("data-status", option);
      btn.setAttribute("role", "option");
      btn.setAttribute(
        "aria-selected",
        option === currentStatus ? "true" : "false"
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
      var trigger = openPicker.querySelector(".dashboard-status-trigger");
      if (trigger) trigger.setAttribute("aria-expanded", "false");
    }
    openPicker = null;
  }

  window.dashboardStatusClose = closePortal;

  function positionMenu(trigger) {
    var rect = trigger.getBoundingClientRect();
    var top = rect.bottom + 4;
    var left = rect.left;
    var minWidth = Math.max(rect.width, 168);

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
    if (window.dashboardPaymentClose) window.dashboardPaymentClose();
    var currentStatus =
      picker.getAttribute("data-current-status") ||
      picker.querySelector(".dashboard-status-label").textContent;
    buildMenu(currentStatus);
    openPicker = picker;
    portal.hidden = false;
    portal.setAttribute("aria-hidden", "false");
    trigger.setAttribute("aria-expanded", "true");
    positionMenu(trigger);
  }

  function applyStatus(picker, status, cssClass) {
    var trigger = picker.querySelector(".dashboard-status-trigger");
    var label = picker.querySelector(".dashboard-status-label");
    if (!trigger || !label) return;
    statusClasses().forEach(function (cls) {
      trigger.classList.remove(cls);
    });
    trigger.classList.add("status-" + cssClass);
    label.textContent = status;
    picker.setAttribute("data-current-status", status);
  }

  window.dashboardApplyStatus = applyStatus;

  function saveStatus(picker, status) {
    var bookingId = picker.getAttribute("data-booking-id");
    var trigger = picker.querySelector(".dashboard-status-trigger");
    if (!bookingId || !trigger) return;

    trigger.disabled = true;
    picker.classList.add("is-saving");

    fetch("/bookings/" + bookingId + "/status", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
      },
      body: JSON.stringify({ status: status }),
    })
      .then(function (res) {
        return res.json().then(function (data) {
          if (!res.ok) throw new Error(data.error || "Could not update status");
          return data;
        });
      })
      .then(function (data) {
        applyStatus(picker, data.status, data.css_class);
        closePortal();
      })
      .catch(function (err) {
        window.alert(err.message || "Could not update status.");
      })
      .finally(function () {
        trigger.disabled = false;
        picker.classList.remove("is-saving");
      });
  }

  pickers.forEach(function (picker) {
    var trigger = picker.querySelector(".dashboard-status-trigger");
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
    var option = event.target.closest(".dashboard-status-option");
    if (!option || !openPicker) return;
    event.stopPropagation();
    var nextStatus = option.getAttribute("data-status");
    if (!nextStatus) return;
    saveStatus(openPicker, nextStatus);
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
