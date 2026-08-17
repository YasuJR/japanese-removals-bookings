(function () {
  var pickers = document.querySelectorAll(".dashboard-status-picker");
  if (!pickers.length) return;

  var openPicker = null;

  function closePicker(picker) {
    if (!picker) return;
    var menu = picker.querySelector(".dashboard-status-menu");
    var trigger = picker.querySelector(".dashboard-status-trigger");
    if (menu) {
      menu.hidden = true;
      menu.style.position = "";
      menu.style.top = "";
      menu.style.left = "";
      menu.style.minWidth = "";
    }
    if (trigger) trigger.setAttribute("aria-expanded", "false");
    if (openPicker === picker) openPicker = null;
  }

  function openMenu(picker) {
    var menu = picker.querySelector(".dashboard-status-menu");
    var trigger = picker.querySelector(".dashboard-status-trigger");
    if (!menu || !trigger) return;
    menu.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    menu.style.position = "fixed";
    var rect = trigger.getBoundingClientRect();
    menu.style.top = rect.bottom + 4 + "px";
    menu.style.left = rect.left + "px";
    menu.style.minWidth = Math.max(rect.width, 176) + "px";
    openPicker = picker;
  }

  function closeAll() {
    pickers.forEach(function (picker) {
      closePicker(picker);
    });
  }

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

  function applyStatus(picker, status, cssClass) {
    var trigger = picker.querySelector(".dashboard-status-trigger");
    var label = picker.querySelector(".dashboard-status-label");
    if (!trigger || !label) return;
    statusClasses().forEach(function (cls) {
      trigger.classList.remove(cls);
    });
    trigger.classList.add("status-" + cssClass);
    label.textContent = status;
    picker.querySelectorAll(".dashboard-status-option").forEach(function (btn) {
      var selected = btn.getAttribute("data-status") === status;
      btn.setAttribute("aria-selected", selected ? "true" : "false");
    });
  }

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
        closePicker(picker);
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
    var menu = picker.querySelector(".dashboard-status-menu");
    if (!trigger || !menu) return;

    trigger.addEventListener("click", function (event) {
      event.stopPropagation();
      var isOpen = openPicker === picker && !menu.hidden;
      closeAll();
      if (!isOpen) {
        openMenu(picker);
      }
    });

    menu.querySelectorAll(".dashboard-status-option").forEach(function (option) {
      option.addEventListener("click", function (event) {
        event.stopPropagation();
        var nextStatus = option.getAttribute("data-status");
        if (!nextStatus) return;
        saveStatus(picker, nextStatus);
      });
    });
  });

  document.addEventListener("click", closeAll);
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeAll();
  });
  window.addEventListener("scroll", closeAll, true);
  window.addEventListener("resize", closeAll);
})();
