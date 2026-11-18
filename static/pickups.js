// Booking form: optional second pickup address ("Add another pickup address").
(function () {
  var flag = document.getElementById("pickup_address_2_enabled");
  var box = document.getElementById("pickup-2-box");
  var addBtn = document.getElementById("pickup-2-add");
  var removeBtn = document.getElementById("pickup-2-remove");
  var input = document.getElementById("pickup_address_2");
  if (!flag || !box || !addBtn || !removeBtn || !input) return;

  function setOpen(open) {
    flag.value = open ? "1" : "0";
    box.hidden = !open;
    addBtn.hidden = open;
    if (open) {
      input.focus();
    } else {
      input.value = "";
    }
  }

  addBtn.addEventListener("click", function () { setOpen(true); });
  removeBtn.addEventListener("click", function () { setOpen(false); });
})();
