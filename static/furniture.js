/* Furniture & items list editor (customer link and office view). */
(function () {
  "use strict";

  var dataEl = document.getElementById("fl-data");
  if (!dataEl) return;
  var cfg = JSON.parse(dataEl.textContent);
  var catalog = cfg.catalog;
  var state = cfg.state;
  var content = state.content;
  var photos = state.photos || [];
  var contact = state.contact || {};
  var isOffice = cfg.mode === "office";
  var STORAGE_KEY = "jr_furniture_lists";

  var dirty = false;
  var saving = false;
  var saveTimer = null;
  var openRooms = {};

  function $(id) { return document.getElementById(id); }
  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (key) {
      var value = attrs[key];
      if (value === null || value === undefined || value === false) return;
      if (key === "text") node.textContent = value;
      else if (key === "class") node.className = value;
      else if (key.indexOf("on") === 0) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : value);
    });
    (children || []).forEach(function (child) {
      if (child === null || child === undefined) return;
      node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    });
    return node;
  }
  function fmt(n) { return (Math.round(n * 10) / 10).toFixed(1); }

  function itemInfo(key) { return catalog.items[key]; }
  function roomType(type) {
    for (var i = 0; i < catalog.room_types.length; i++) {
      if (catalog.room_types[i].type === type) return catalog.room_types[i];
    }
    return null;
  }

  // ------------------------------------------------------------ estimate
  function customM3(item) {
    var l = parseFloat(item.length_cm), w = parseFloat(item.width_cm), h = parseFloat(item.height_cm);
    if (!(l > 0 && w > 0 && h > 0)) return null;
    return (l * w * h) / 1000000;
  }
  // Items keep the m³ they were added with (set by the server on save); new ones use today's standard.
  function unitM3(room, key, info) {
    var units = room.unit_m3 || {};
    return typeof units[key] === "number" ? units[key] : info.m3;
  }
  function applyUnits(units) {
    if (!units) return;
    content.rooms.forEach(function (room) {
      var saved = units[room.id] || {};
      var next = {};
      Object.keys(room.items).forEach(function (key) {
        if (typeof saved[key] === "number") next[key] = saved[key];
        else if (room.unit_m3 && typeof room.unit_m3[key] === "number") next[key] = room.unit_m3[key];
      });
      room.unit_m3 = next;
    });
  }
  function computeSummary() {
    var total = 0, count = 0, unsized = 0;
    content.rooms.forEach(function (room) {
      Object.keys(room.items).forEach(function (key) {
        var qty = room.items[key] || 0;
        var info = itemInfo(key);
        if (!info || qty <= 0) return;
        total += unitM3(room, key, info) * qty;
        count += qty;
      });
    });
    content.custom_items.forEach(function (item) {
      var qty = parseInt(item.qty, 10) || 1;
      if (!item.name) return;
      count += qty;
      var each = customM3(item);
      if (each === null) unsized += qty; else total += each * qty;
    });
    return { m3: total, count: count, unsized: unsized };
  }
  function updateSummary() {
    var s = computeSummary();
    $("fl-m3").textContent = fmt(s.m3);
    $("fl-bar-m3").textContent = fmt(s.m3);
    $("fl-bar-count").textContent = String(s.count);
    var detail = s.count + " item" + (s.count === 1 ? "" : "s") + " in your list.";
    if (s.unsized) detail += " " + s.unsized + " other item" + (s.unsized === 1 ? " has" : "s have") + " no size, so not included in the m³.";
    $("fl-estimate-detail").textContent = detail;
  }

  // ------------------------------------------------------------ saving
  function setSaveState(text, kind) {
    var node = $("fl-save-state");
    node.textContent = text;
    node.className = "fl-save-state" + (kind ? " fl-save-" + kind : "");
  }
  function markDirty() {
    dirty = true;
    setSaveState("Unsaved changes", "dirty");
    updateSummary();
    clearTimeout(saveTimer);
    saveTimer = setTimeout(function () { save(false); }, 2500);
  }
  function payload(submit) {
    return { content: content, contact: contact, submit: !!submit };
  }
  function rememberList() {
    if (isOffice || !cfg.own_url) return;
    try {
      var saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
      saved[cfg.public_id] = { url: cfg.own_url, at: Date.now() };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
    } catch (e) { /* storage unavailable */ }
  }
  function save(submit) {
    if (saving) {
      clearTimeout(saveTimer);
      saveTimer = setTimeout(function () { save(submit); }, 800);
      return Promise.resolve(false);
    }
    clearTimeout(saveTimer);
    saving = true;
    setSaveState(submit ? "Sending…" : "Saving…", "busy");
    return fetch(cfg.endpoints.save, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(payload(submit)),
    })
      .then(function (res) {
        return res.json().then(function (body) {
          if (!res.ok || !body.ok) throw new Error(body.error || "Save failed");
          return body;
        });
      })
      .then(function (body) {
        saving = false;
        dirty = false;
        state.status = body.status;
        applyUnits(body.units);
        updateSummary();
        rememberList();
        setSaveState("Saved " + new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }), "ok");
        var chip = document.querySelector("[data-status-chip]");
        if (chip) {
          chip.textContent = body.status_label;
          chip.className = "fl-chip fl-chip-" + body.status;
        }
        var line = document.querySelector("[data-saved-line]");
        if (line && body.updated_display) line.textContent = "Last saved " + body.updated_display;
        if (submit) {
          var done = $("fl-done");
          if (done) { done.hidden = false; done.scrollIntoView({ behavior: "smooth", block: "center" }); }
          var submitBtn = $("fl-submit");
          if (submitBtn) submitBtn.textContent = "Update list";
        }
        return true;
      })
      .catch(function (err) {
        saving = false;
        setSaveState("Not saved — check your connection", "error");
        if (submit) alert("Sorry, we couldn't send your list. Please check your connection and try again.");
        return false;
      });
  }

  // ------------------------------------------------------------ rooms
  function stepper(value, onChange, label) {
    var input = el("input", {
      type: "number", min: "0", max: "200", inputmode: "numeric", value: String(value),
      "aria-label": label + " quantity", class: "fl-qty",
    });
    function set(n) {
      n = Math.max(0, Math.min(200, n | 0));
      input.value = String(n);
      onChange(n);
    }
    input.addEventListener("change", function () { set(parseInt(input.value, 10) || 0); });
    return el("div", { class: "fl-stepper" }, [
      el("button", { type: "button", class: "fl-step", "aria-label": "Remove one " + label, onclick: function () { set((parseInt(input.value, 10) || 0) - 1); } }, ["−"]),
      input,
      el("button", { type: "button", class: "fl-step", "aria-label": "Add one " + label, onclick: function () { set((parseInt(input.value, 10) || 0) + 1); } }, ["+"]),
    ]);
  }

  function roomCount(room) {
    return Object.keys(room.items).reduce(function (sum, key) { return sum + (room.items[key] || 0); }, 0);
  }

  function renderRooms() {
    var wrap = $("fl-rooms");
    wrap.innerHTML = "";
    content.rooms.forEach(function (room, index) {
      var type = roomType(room.type);
      if (!type) return;
      var countNode = el("span", { class: "fl-room-count" });
      function refreshCount() {
        var c = roomCount(room);
        countNode.textContent = c ? c + " item" + (c === 1 ? "" : "s") : "";
      }
      refreshCount();
      var list = el("div", { class: "fl-items" });
      var hasBeds = type.items.some(function (key) { return /^bed_/.test(key); });
      var extraBeds = null;
      if (hasBeds) {
        list.appendChild(el("p", { class: "fl-group-title" }, ["Beds — one entry per bed"]));
        list.appendChild(el("p", { class: "fl-group-hint" }, ["A bed with its mattress counts once. Use “frame only” or “mattress only” just for items moving on their own."]));
        var anyExtra = type.items.some(function (key) { return /^(bed_frame_|mattress_)/.test(key) && room.items[key]; });
        extraBeds = el("details", { class: "fl-subgroup", open: anyExtra }, [
          el("summary", { class: "fl-subgroup-head", text: "Bed frame only / mattress only" }),
        ]);
      }
      var otherStarted = false;
      type.items.forEach(function (key) {
        var info = itemInfo(key);
        var isBedMain = /^bed_(?!frame_)/.test(key);
        var isBedExtra = /^(bed_frame_|mattress_)/.test(key);
        if (hasBeds && !isBedMain && !isBedExtra && !otherStarted) {
          list.appendChild(extraBeds);
          list.appendChild(el("p", { class: "fl-group-title" }, ["Other furniture"]));
          otherStarted = true;
        }
        var qty = room.items[key] || 0;
        // Office: yellow = changed after the first submission (qty before, 0 = added).
        var roomChanges = (cfg.changes || {})[room.id] || {};
        var changed = Object.prototype.hasOwnProperty.call(roomChanges, key);
        var row = el("div", { class: "fl-item" + (qty ? " fl-item-on" : "") + (changed ? " fl-item-changed" : "") }, [
          el("div", { class: "fl-item-text" }, [
            el("span", { class: "fl-item-label", text: info.label }),
            info.hint ? el("span", { class: "fl-item-hint", text: info.hint }) : null,
            changed ? el("span", { class: "fl-was", text: roomChanges[key] ? "was × " + roomChanges[key] : "added after first submission" }) : null,
          ]),
          stepper(qty, function (n) {
            if (n > 0) room.items[key] = n;
            else {
              delete room.items[key];
              if (room.unit_m3) delete room.unit_m3[key];
            }
            row.className = "fl-item" + (n ? " fl-item-on" : "") + (changed ? " fl-item-changed" : "");
            refreshCount();
            markDirty();
          }, info.label),
        ]);
        (isBedExtra ? extraBeds : list).appendChild(row);
      });
      if (hasBeds && !otherStarted) list.appendChild(extraBeds);
      var details = el("details", { class: "fl-room" }, [
        el("summary", { class: "fl-room-head" }, [
          el("span", { class: "fl-room-name", text: room.label }),
          countNode,
        ]),
        list,
        el("div", { class: "fl-room-tools" }, [
          el("button", { type: "button", class: "fl-link", onclick: function () {
            var name = prompt("Room name", room.label);
            if (name && name.trim()) { room.label = name.trim().slice(0, 60); renderRooms(); renderCustom(); markDirty(); }
          } }, ["Rename"]),
          el("button", { type: "button", class: "fl-link fl-link-danger", onclick: function () {
            if (roomCount(room) && !confirm("Remove " + room.label + " and its items?")) return;
            content.rooms.splice(index, 1);
            renderRooms(); renderCustom(); markDirty();
          } }, ["Remove room"]),
        ]),
      ]);
      if (openRooms[room.id]) details.open = true;
      details.addEventListener("toggle", function () { openRooms[room.id] = details.open; });
      wrap.appendChild(details);
    });
  }

  function setupAddRoom() {
    var select = $("fl-add-room-type");
    catalog.room_types.forEach(function (type) {
      select.appendChild(el("option", { value: type.type, text: type.label }));
    });
    $("fl-add-room").addEventListener("click", function () {
      if (content.rooms.length >= 30) { alert("That's a lot of rooms! Please use notes for the rest."); return; }
      var type = roomType(select.value);
      var same = content.rooms.filter(function (r) { return r.type === type.type; }).length;
      var label = type.label + (same ? " " + (same + 1) : "");
      if (type.type === "bedroom") label = "Bedroom " + (same + 1);
      var id = "r" + Date.now().toString(36);
      content.rooms.push({ id: id, type: type.type, label: label, items: {} });
      openRooms[id] = true;
      renderRooms(); renderCustom(); markDirty();
    });
  }

  // ------------------------------------------------------------ custom items
  function renderCustom() {
    var wrap = $("fl-custom");
    wrap.innerHTML = "";
    content.custom_items.forEach(function (item, index) {
      function field(label, key, attrs) {
        var input = el("input", Object.assign({ value: item[key] === null || item[key] === undefined ? "" : String(item[key]) }, attrs || {}));
        input.addEventListener("input", function () {
          item[key] = input.value;
          markDirty();
        });
        return el("label", { class: "fl-mini" }, [label, input]);
      }
      var roomSelect = el("select", { "aria-label": "Room" }, [el("option", { value: "", text: "Room (optional)" })]);
      content.rooms.forEach(function (room) {
        roomSelect.appendChild(el("option", { value: room.label, text: room.label, selected: item.room === room.label }));
      });
      roomSelect.addEventListener("change", function () { item.room = roomSelect.value; markDirty(); });
      var each = customM3(item);
      wrap.appendChild(el("div", { class: "fl-custom-item" }, [
        el("div", { class: "fl-custom-row" }, [
          field("Item", "name", { type: "text", maxlength: "200", placeholder: "e.g. Fish tank", class: "fl-custom-name" }),
          el("div", { class: "fl-mini" }, ["Qty", stepper(parseInt(item.qty, 10) || 1, function (n) { item.qty = Math.max(1, n); markDirty(); }, item.name || "item")]),
        ]),
        el("div", { class: "fl-dims" }, [
          field("Length cm", "length_cm", { type: "number", min: "1", max: "1000", inputmode: "decimal" }),
          field("Width cm", "width_cm", { type: "number", min: "1", max: "1000", inputmode: "decimal" }),
          field("Height cm", "height_cm", { type: "number", min: "1", max: "1000", inputmode: "decimal" }),
        ]),
        el("div", { class: "fl-custom-row" }, [
          roomSelect,
          field("Note", "note", { type: "text", maxlength: "200", placeholder: "Optional" }),
        ]),
        el("div", { class: "fl-room-tools" }, [
          el("span", { class: "fl-muted", text: each === null ? "No size — not counted in m³" : "≈ " + fmt(each * (parseInt(item.qty, 10) || 1)) + " m³" }),
          el("button", { type: "button", class: "fl-link fl-link-danger", onclick: function () {
            content.custom_items.splice(index, 1); renderCustom(); markDirty();
          } }, ["Remove"]),
        ]),
      ]));
    });
  }

  // ------------------------------------------------------------ photos
  function photoUrl(id, template) {
    return template.replace(/\/0(\/delete)?$/, "/" + id + "$1");
  }
  function renderPhotos() {
    var wrap = $("fl-photos");
    wrap.innerHTML = "";
    photos.forEach(function (photo) {
      var src = photoUrl(photo.id, cfg.endpoints.photo);
      wrap.appendChild(el("figure", { class: "fl-photo" }, [
        el("a", { href: src, target: "_blank", rel: "noopener" }, [el("img", { src: src, alt: "Uploaded photo", loading: "lazy" })]),
        el("button", { type: "button", class: "fl-photo-del", "aria-label": "Delete photo", onclick: function () {
          if (!confirm("Delete this photo?")) return;
          fetch(photoUrl(photo.id, cfg.endpoints.delete_photo), { method: "POST", credentials: "same-origin" })
            .then(function (res) { return res.json(); })
            .then(function (body) {
              if (body.ok) {
                photos = photos.filter(function (p) { return p.id !== photo.id; });
                renderPhotos();
              }
            });
        } }, ["×"]),
      ]));
    });
  }
  function shrinkImage(file) {
    // Resize big phone photos before upload (faster on mobile data). Server re-checks everything.
    return new Promise(function (resolve) {
      if (!window.createImageBitmap || !/^image\/(jpeg|png|webp)$/.test(file.type) || file.size < 1500000) {
        resolve(file);
        return;
      }
      createImageBitmap(file, { imageOrientation: "from-image" }).then(function (bitmap) {
        var scale = Math.min(1, 2000 / Math.max(bitmap.width, bitmap.height));
        var canvas = document.createElement("canvas");
        canvas.width = Math.round(bitmap.width * scale);
        canvas.height = Math.round(bitmap.height * scale);
        canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
        canvas.toBlob(function (blob) { resolve(blob && blob.size < file.size ? blob : file); }, "image/jpeg", 0.86);
      }).catch(function () { resolve(file); });
    });
  }
  function setupPhotos() {
    var input = $("fl-photo-input");
    var status = $("fl-photo-status");
    input.addEventListener("change", function () {
      var files = Array.prototype.slice.call(input.files || []);
      input.value = "";
      if (!files.length) return;
      var room = cfg.max_photos - photos.length;
      if (room <= 0) { status.textContent = "You've reached the maximum of " + cfg.max_photos + " photos."; return; }
      if (files.length > room) {
        status.textContent = "Only the first " + room + " photo(s) will be added (max " + cfg.max_photos + ").";
        files = files.slice(0, room);
      }
      var done = 0, failed = [];
      function next() {
        if (!files.length) {
          status.textContent = done + " photo" + (done === 1 ? "" : "s") + " added." + (failed.length ? " " + failed.join(" ") : "");
          renderPhotos();
          if (done) rememberList();
          return;
        }
        var file = files.shift();
        status.textContent = "Uploading " + (file.name || "photo") + "…";
        if (file.size > 15 * 1024 * 1024) {
          failed.push((file.name || "A photo") + " is over 15 MB.");
          next();
          return;
        }
        shrinkImage(file).then(function (blob) {
          var form = new FormData();
          form.append("photo", blob, (file.name || "photo").replace(/\.[^.]+$/, "") + ".jpg");
          return fetch(cfg.endpoints.upload, { method: "POST", body: form, credentials: "same-origin" })
            .then(function (res) { return res.json().catch(function () { return { ok: false, error: "Upload failed." }; }); })
            .then(function (body) {
              if (body.ok) { photos.push(body.photo); done += 1; renderPhotos(); }
              else failed.push((file.name || "A photo") + ": " + (body.error || "upload failed") + ".");
            });
        }).catch(function () {
          failed.push((file.name || "A photo") + ": upload failed.");
        }).then(next);
      }
      next();
    });
  }

  // ------------------------------------------------------------ misc
  function setupContact() {
    document.querySelectorAll("[data-contact]").forEach(function (input) {
      var key = input.getAttribute("data-contact");
      input.value = contact[key] || "";
      input.addEventListener("input", function () { contact[key] = input.value; markDirty(); });
    });
  }
  function setupCopy() {
    document.querySelectorAll("[data-copy-target]").forEach(function (button) {
      button.addEventListener("click", function () {
        var input = $(button.getAttribute("data-copy-target"));
        var text = input.value;
        function ok() { button.textContent = "Copied"; setTimeout(function () { button.textContent = "Copy"; }, 1800); }
        if (navigator.clipboard && window.isSecureContext) {
          navigator.clipboard.writeText(text).then(ok, function () { input.select(); document.execCommand("copy"); ok(); });
        } else { input.select(); document.execCommand("copy"); ok(); }
      });
    });
  }

  function init() {
    var notes = $("fl-notes");
    notes.value = content.notes || "";
    notes.addEventListener("input", function () { content.notes = notes.value; markDirty(); });
    $("fl-add-custom").addEventListener("click", function () {
      if (content.custom_items.length >= 100) return;
      content.custom_items.push({ name: "", qty: 1, room: "", length_cm: "", width_cm: "", height_cm: "", note: "" });
      renderCustom();
      var inputs = document.querySelectorAll(".fl-custom-name");
      if (inputs.length) inputs[inputs.length - 1].focus();
    });
    $("fl-save").addEventListener("click", function () { save(false); });
    var submit = $("fl-submit");
    if (submit) submit.addEventListener("click", function () { save(true); });
    // "Save & Return to Quote" (data-always-save) always saves first and only leaves when the
    // save worked, so nothing typed is lost. Other return links save only unsaved changes.
    document.querySelectorAll("[data-return-link]").forEach(function (link) {
      link.addEventListener("click", function (event) {
        var always = link.hasAttribute("data-always-save");
        if (!dirty && !always) return;
        event.preventDefault();
        if (link.getAttribute("aria-busy") === "true") return;
        link.setAttribute("aria-busy", "true");
        (function attempt(tries) {
          if (saving && tries < 40) { setTimeout(function () { attempt(tries + 1); }, 250); return; }
          save(false).then(function (ok) {
            if (ok) { window.location.href = link.href; return; }
            link.removeAttribute("aria-busy");
            if (always) alert("Sorry, we couldn't save your list. Please check your connection and try again.");
          });
        })(0);
      });
    });
    window.addEventListener("beforeunload", function (event) {
      if (dirty) { event.preventDefault(); event.returnValue = ""; }
    });
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden" && dirty && !saving) {
        try {
          fetch(cfg.endpoints.save, {
            method: "POST", keepalive: true, credentials: "same-origin",
            headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload(false)),
          });
          dirty = false;
        } catch (e) { /* ignore */ }
      }
    });
    if (state.status !== "not_started") rememberList();
    setupContact();
    setupAddRoom();
    setupPhotos();
    setupCopy();
    renderRooms();
    renderCustom();
    renderPhotos();
    updateSummary();
    if (state.status === "submitted" && !isOffice) setSaveState("Sent ✓", "ok");
  }

  init();
})();
