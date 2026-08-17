(function () {
  function dailyJobsUrl(iso) {
    var params = new URLSearchParams(window.location.search);
    var back = new URLSearchParams();
    ["view", "year", "month", "day", "status", "crew", "truck", "payment"].forEach(
      function (key) {
        var value = params.get(key);
        if (value) back.set(key, value);
      }
    );
    var url = "/calendar/daily/" + encodeURIComponent(iso);
    var qs = back.toString();
    return qs ? url + "?" + qs : url;
  }

  function isBookingClick(target) {
    return (
      target &&
      target.closest &&
      target.closest(".calendar-booking, .calendar-week-booking")
    );
  }

  function openDailyJobs(iso, event) {
    if (isBookingClick(event && event.target)) return;
    if (!iso) return;
    window.location.href = dailyJobsUrl(iso);
  }

  document.querySelectorAll(".calendar-day").forEach(function (cell) {
    cell.addEventListener("click", function (event) {
      openDailyJobs(cell.getAttribute("data-date"), event);
    });
    cell.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openDailyJobs(cell.getAttribute("data-date"), event);
      }
    });
  });

  document.querySelectorAll(".calendar-week-col").forEach(function (col) {
    col.addEventListener("click", function (event) {
      openDailyJobs(col.getAttribute("data-date"), event);
    });
  });
})();
