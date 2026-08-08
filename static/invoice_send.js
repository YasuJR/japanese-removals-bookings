(function () {
  var form = document.querySelector(".booking-form");
  if (!form) return;

  var emailInput = form.querySelector('input[name="email"]');
  var phoneInput = form.querySelector('input[name="phone"]');
  var sendBtn = form.querySelector(".invoice-send-btn");
  if (!sendBtn) return;

  var defaultEmail = (sendBtn.getAttribute("data-default-email") || "")
    .trim()
    .toLowerCase();
  var defaultPhone = sendBtn.getAttribute("data-default-phone") || "";
  var companyPhone = sendBtn.getAttribute("data-company-phone") || "";
  var emailPattern = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

  function normalizePhoneDigits(phone) {
    var digits = (phone || "").replace(/\D/g, "");
    if (digits.indexOf("61") === 0 && digits.length >= 11) {
      digits = "0" + digits.slice(2);
    }
    return digits;
  }

  function formatPhoneDisplay(phone) {
    var digits = normalizePhoneDigits(phone);
    if (digits.length === 10 && digits.charAt(0) === "0") {
      return digits.slice(0, 4) + " " + digits.slice(4, 7) + " " + digits.slice(7);
    }
    return (phone || "").trim();
  }

  function isPlaceholderEmail(value) {
    var text = (value || "").trim().toLowerCase();
    if (!text) return false;
    if (defaultEmail && text === defaultEmail) return true;
    return text === "info@japaneseremovals.com.au";
  }

  function isValidEmailFormat(value) {
    return emailPattern.test((value || "").trim());
  }

  function isValidCustomerEmail(value) {
    var text = (value || "").trim();
    if (!isValidEmailFormat(text)) return false;
    return !isPlaceholderEmail(text);
  }

  function isPlaceholderPhone(value) {
    var digits = normalizePhoneDigits(value);
    if (!digits) return true;
    var companyNumbers = [];
    [defaultPhone, companyPhone, "0481 089 573"].forEach(function (candidate) {
      var normalized = normalizePhoneDigits(candidate);
      if (normalized) companyNumbers.push(normalized);
    });
    return companyNumbers.indexOf(digits) !== -1;
  }

  function isValidCustomerPhone(value) {
    var text = (value || "").trim();
    if (!text) return false;
    if (isPlaceholderPhone(text)) return false;
    return normalizePhoneDigits(text).length >= 9;
  }

  function resolveFromForm() {
    var email = emailInput ? emailInput.value.trim() : "";
    var phone = phoneInput ? phoneInput.value.trim() : "";

    if (isValidCustomerEmail(email)) {
      return {
        canSend: true,
        method: "email",
        destination: email,
        destinationDisplay: email,
      };
    }

    if (isValidCustomerPhone(phone)) {
      return {
        canSend: true,
        method: "sms",
        destination: phone,
        destinationDisplay: formatPhoneDisplay(phone),
      };
    }

    return {
      canSend: false,
      method: "",
      destination: "",
      destinationDisplay: "",
    };
  }

  function syncSendButton() {
    var resolved = resolveFromForm();
    sendBtn.disabled = !resolved.canSend;
    if (resolved.canSend) {
      sendBtn.removeAttribute("title");
      sendBtn.setAttribute("data-send-method", resolved.method);
      sendBtn.setAttribute("data-send-destination", resolved.destination);
      sendBtn.setAttribute(
        "data-send-destination-display",
        resolved.destinationDisplay
      );
    } else {
      sendBtn.setAttribute(
        "title",
        "Customer email or phone number required."
      );
      sendBtn.removeAttribute("data-send-method");
      sendBtn.removeAttribute("data-send-destination");
      sendBtn.removeAttribute("data-send-destination-display");
    }
  }

  sendBtn.addEventListener("click", function (event) {
    syncSendButton();
    if (sendBtn.disabled) {
      event.preventDefault();
      return;
    }
    var method = sendBtn.getAttribute("data-send-method") || "email";
    var destination =
      sendBtn.getAttribute("data-send-destination-display") ||
      sendBtn.getAttribute("data-send-destination") ||
      "";
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
