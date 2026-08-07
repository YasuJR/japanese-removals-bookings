(function () {
  document.querySelectorAll(".invoice-send-btn").forEach(function (btn) {
    btn.addEventListener("click", function (event) {
      if (btn.disabled) return;
      var method = btn.getAttribute("data-send-method") || "email";
      var destination = btn.getAttribute("data-send-destination") || "";
      var label = method === "sms" ? "SMS:" : "Email:";
      var message = label + "\n" + destination + "\n\nSend invoice?";
      if (!window.confirm(message)) {
        event.preventDefault();
      }
    });
  });
})();
