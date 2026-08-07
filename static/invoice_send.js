(function () {
  var form = document.querySelector(".booking-form");
  if (!form) return;

  var emailInput = form.querySelector('input[name="email"]');
  var phoneInput = form.querySelector('input[name="phone"]');
  var sendBtn = form.querySelector(".invoice-send-btn");
  if (!sendBtn) return;

  var defaultEmail = (sendBtn.getAttribute("data-default-email") || "").trim().toLowerCase();

  function isPlaceholderEmail(value) {
    var text = (value || "").trim().toLowerCase();
    if (!text) return false;
    if (defaultEmail && text === defaultEmail) return true;
    return text === "info@japaneseremovals.com.au";
  }

  function resolveFromForm() {
    var email = emailInput ? emailInput.value.trim() : "";
    var phone = phoneInput ? phoneInput.value.trim() : "";
    if (!email && !phone) {
      return { canSend: false, method: "", destination: "" };
    }
    if (email && !isPlaceholderEmail(email)) {
      return { canSend: true, method: "email", destination: email };
    }
    if (phone) {
      return { canSend: true, method: "sms", destination: phone };
    }
    if (email) {
      return { canSend: true, method: "email", destination: email };
    }
    return { canSend: false, method: "", destination: "" };
  }

  function syncSendButton() {
    var resolved = resolveFromForm();
    sendBtn.disabled = !resolved.canSend;
    if (resolved.canSend) {
      sendBtn.removeAttribute("title");
      sendBtn.setAttribute("data-send-method", resolved.method);
      sendBtn.setAttribute("data-send-destination", resolved.destination);
    } else {
      sendBtn.setAttribute(
        "title",
        "Customer email or phone number required."
      );
    }
  }

  sendBtn.addEventListener("click", function (event) {
    syncSendButton();
    if (sendBtn.disabled) return;
    var method = sendBtn.getAttribute("data-send-method") || "email";
    var destination = sendBtn.getAttribute("data-send-destination") || "";
    var label = method === "sms" ? "SMS:" : "Email:";
    var message = label + "\n" + destination + "\n\nSend invoice?";
    if (!window.confirm(message)) {
      event.preventDefault();
    }
  });

  if (emailInput) {
    emailInput.addEventListener("input", syncSendButton);
    emailInput.addEventListener("change", syncSendButton);
  }
  if (phoneInput) {
    phoneInput.addEventListener("input", syncSendButton);
    phoneInput.addEventListener("change", syncSendButton);
  }

  syncSendButton();
})();
