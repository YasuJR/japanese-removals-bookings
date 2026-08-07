(function () {
  var panel = document.getElementById("calendar-day-panel");
  var content = document.getElementById("calendar-day-panel-content");
  var title = document.getElementById("calendar-day-panel-title");
  var newLink = document.getElementById("calendar-new-booking-link");
  var closeBtn = document.getElementById("calendar-day-panel-close");
  var dayData = window.CALENDAR_DAY_DATA || {};

  if (!panel || !content) return;

  function formatDay(iso) {
    try {
      var parts = iso.split("-");
      var d = new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10));
      return d.toLocaleDateString("en-AU", {
        weekday: "long",
        day: "numeric",
        month: "long",
        year: "numeric",
      });
    } catch (e) {
      return iso;
    }
  }

  function openDayPanel(iso, event) {
    if (event && event.target && event.target.closest(".calendar-booking")) {
      return;
    }
    var bookings = dayData[iso] || [];
    title.textContent = formatDay(iso);
    newLink.href = "/bookings/new?move_date=" + encodeURIComponent(iso);

    var html = [];
    html.push("<p><strong>Date:</strong> " + formatDay(iso) + "</p>");
    html.push("<p><strong>Bookings:</strong> " + bookings.length + "</p>");

    if (bookings.length) {
      html.push("<ul class='calendar-panel-bookings'>");
      bookings.forEach(function (b) {
        html.push(
          "<li><a href='" +
            b.edit_url +
            "'><strong>" +
            b.customer_name +
            "</strong> · " +
            b.time_range +
            "</a></li>"
        );
      });
      html.push("</ul>");
      var crews = {};
      var trucks = {};
      bookings.forEach(function (b) {
        (b.crew_list || []).forEach(function (c) {
          crews[c] = true;
        });
        if (b.truck_assigned) trucks[b.truck_assigned] = true;
      });
      html.push(
        "<p><strong>Assigned crew:</strong> " +
          (Object.keys(crews).join(", ") || "—") +
          "</p>"
      );
      html.push(
        "<p><strong>Assigned trucks:</strong> " +
          (Object.keys(trucks).join(", ") || "—") +
          "</p>"
      );
      var conflicts = bookings.filter(function (b) {
        return b.has_conflict;
      });
      if (conflicts.length) {
        html.push("<p class='calendar-panel-conflict'><strong>Conflicts:</strong> " + conflicts.length + " overlapping booking(s)</p>");
      } else {
        html.push("<p><strong>Available time:</strong> See week view for slot detail</p>");
      }
    } else {
      html.push("<p class='muted'>No bookings this day.</p>");
      html.push("<p><strong>Available time:</strong> Full day open</p>");
    }

    content.innerHTML = html.join("");
    panel.hidden = false;
  }

  function closePanel() {
    panel.hidden = true;
  }

  document.querySelectorAll(".calendar-day").forEach(function (cell) {
    cell.addEventListener("click", function (event) {
      openDayPanel(cell.getAttribute("data-date"), event);
    });
    cell.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openDayPanel(cell.getAttribute("data-date"), event);
      }
    });
  });

  if (closeBtn) closeBtn.addEventListener("click", closePanel);
  panel.addEventListener("click", function (event) {
    if (event.target === panel) closePanel();
  });
})();
