/*
 * Japanese Removals website — "Add your furniture list" button for the quote form.
 *
 * Paste once on the WordPress site (footer or a Custom HTML block):
 *   <script src="https://japanese-removals-bookings.onrender.com/static/furniture-embed.js" defer></script>
 *
 * - Adds an optional "Add your furniture list" button to every [data-quote-form].
 * - The form's own draft autosave (jr_quote_draft_v1) keeps everything typed,
 *   so going to the list and pressing "Back to booking form" loses nothing.
 * - On return (?furniture_list=FL-XXXXXX) the reference is remembered and the
 *   form re-opens at step 2.
 * - On send, "Furniture list: FL-XXXXXX" is added to the message, so the enquiry
 *   email carries the reference and the booking app links the list automatically.
 *   Only the reference is shared with the website — never the private edit link.
 */
(function () {
  "use strict";

  var script = document.currentScript;
  var APP = (script && script.getAttribute("data-app")) ||
    (script && script.src ? new URL(script.src).origin : "https://japanese-removals-bookings.onrender.com");
  APP = APP.replace(/\/+$/, "");
  var REF_KEY = "jr_furniture_list_ref";
  var REF_RE = /^FL-[A-HJ-NP-Z2-9]{6}$/;
  var MAX_AGE = 30 * 864e5;

  function store() {
    try { return window.localStorage; } catch (e) { return null; }
  }
  function getRef() {
    var s = store();
    if (!s) return "";
    try {
      var data = JSON.parse(s.getItem(REF_KEY) || "null");
      if (!data || !REF_RE.test(data.id) || Date.now() - data.t > MAX_AGE) return "";
      return data.id;
    } catch (e) { return ""; }
  }
  function setRef(id) {
    var s = store();
    if (s) { try { s.setItem(REF_KEY, JSON.stringify({ id: id, t: Date.now() })); } catch (e) {} }
  }
  function clearRef() {
    var s = store();
    if (s) { try { s.removeItem(REF_KEY); } catch (e) {} }
  }

  function pageUrl() {
    var url = new URL(window.location.href);
    url.searchParams.delete("furniture_list");
    url.hash = "";
    return url.toString();
  }
  function listUrl(ref) {
    var url = new URL(APP + "/furniture");
    url.searchParams.set("return", pageUrl());
    if (ref) url.searchParams.set("code", ref);
    return url.toString();
  }

  function injectStyles() {
    if (document.getElementById("jr-fl-embed-css")) return;
    var css = document.createElement("style");
    css.id = "jr-fl-embed-css";
    css.textContent =
      ".jr-fl-card{grid-column:1/-1;border:2px solid #0f3b2e;background:#f1f7f4;border-radius:14px;padding:18px;margin:10px 0}" +
      ".jr-fl-card h3{margin:0 0 6px;font-size:1.45rem;line-height:1.2;font-weight:800;color:#0f3b2e}" +
      ".jr-fl-card p{margin:0 0 12px;font-size:.98rem;line-height:1.5;color:#1d3b31}" +
      ".jr-fl-saved{display:flex;align-items:center;gap:6px;margin:0 0 12px;font-size:1rem;font-weight:800;color:#0f3b2e}" +
      ".jr-fl-actions{display:flex;flex-wrap:wrap;gap:10px;align-items:center}" +
      ".jr-fl-btn{display:inline-flex;align-items:center;justify-content:center;min-height:48px;padding:12px 22px;border-radius:12px;background:#0f3b2e;color:#fff!important;font-weight:800;text-decoration:none!important;border:0;cursor:pointer;font-size:1rem}" +
      ".jr-fl-btn:hover{background:#1d5c47}" +
      ".jr-fl-remove{background:none;border:0;color:#5b6b65;text-decoration:underline;cursor:pointer;font-size:.85rem;padding:8px 4px}" +
      ".jr-fl-id{display:block;margin-top:10px;font-size:.78rem;color:#5b6b65}" +
      ".jr-fl-inline{display:inline-block;margin-top:6px;color:#0f3b2e;font-weight:700}";
    document.head.appendChild(css);
  }

  function saveFormDraft(form) {
    // The theme saves its draft on input/change; fire one so the latest values are stored.
    try { form.dispatchEvent(new Event("change", { bubbles: true })); } catch (e) {}
  }

  function renderCard(card, form) {
    var ref = getRef();
    card.innerHTML = "";

    var title = document.createElement("h3");
    title.textContent = "Furniture List";
    card.appendChild(title);

    if (ref) {
      var saved = document.createElement("p");
      saved.className = "jr-fl-saved";
      saved.textContent = "✓ Saved — will be included with your quote request";
      card.appendChild(saved);
    } else {
      var text = document.createElement("p");
      text.textContent = "List your furniture room by room (add photos if you like) for a more accurate quote. Your form details are kept while you do it. Optional.";
      card.appendChild(text);
    }

    var actions = document.createElement("div");
    actions.className = "jr-fl-actions";
    var button = document.createElement("a");
    button.className = "jr-fl-btn";
    button.href = listUrl(ref);
    button.setAttribute("data-track", "furniture_list");
    button.addEventListener("click", function () { saveFormDraft(form); });
    button.textContent = ref ? "Edit Furniture List" : "Add Furniture List";
    actions.appendChild(button);
    if (ref) {
      var remove = document.createElement("button");
      remove.type = "button";
      remove.className = "jr-fl-remove";
      remove.textContent = "Don't include it";
      remove.addEventListener("click", function () { clearRef(); renderAll(); });
      actions.appendChild(remove);
    }
    card.appendChild(actions);

    if (ref) {
      var idline = document.createElement("span");
      idline.className = "jr-fl-id";
      idline.textContent = "List ID: " + ref;
      card.appendChild(idline);
    }
  }

  var cards = [];
  function renderAll() {
    cards.forEach(function (entry) { renderCard(entry.card, entry.form); });
    document.querySelectorAll("[data-jr-fl-inline]").forEach(function (a) {
      var ref = getRef();
      a.href = listUrl(ref);
      a.textContent = ref ? "Edit your furniture list (" + ref + ")" : "Add your furniture list (optional)";
    });
  }

  function appendReference(form) {
    var ref = getRef();
    if (!ref) return;
    var message = form.querySelector('[name="message"]');
    var line = "Furniture list: " + ref;
    if (message && message.value.indexOf(ref) === -1) {
      var max = parseInt(message.getAttribute("maxlength") || "0", 10) || 0;
      var value = message.value.replace(/\s+$/, "");
      var next = (value ? value + "\n\n" : "") + line;
      if (max && next.length > max) next = value.slice(0, Math.max(0, max - line.length - 2)) + "\n\n" + line;
      message.value = next;
    }
    var hidden = form.querySelector('input[name="furniture_list"]');
    if (!hidden) {
      hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = "furniture_list";
      form.appendChild(hidden);
    }
    hidden.value = ref;
  }

  function enhance(form) {
    if (form.getAttribute("data-jr-fl") === "1") return;
    form.setAttribute("data-jr-fl", "1");
    var step1 = form.querySelector('[data-step="1"] .fields');
    var step2 = form.querySelector('[data-step="2"] .fields') || form.querySelector(".fields");
    var messageField = form.querySelector('[name="message"]');
    function mkCard() {
      var c = document.createElement("div");
      c.className = "jr-fl-card field field--wide";
      cards.push({ card: c, form: form });
      return c;
    }
    // Step 2: just before "Tell us about your move".
    if (step2) {
      var card2 = mkCard();
      var anchor = messageField && messageField.closest(".field");
      if (anchor && step2.contains(anchor)) step2.insertBefore(card2, anchor);
      else step2.appendChild(card2);
    }
    // Step 1: at the end of the essentials, so it's reachable without opening step 2. renderAll keeps
    // both cards in sync (add on one step → both show "Saved").
    if (step1 && step1 !== step2) step1.appendChild(mkCard());
    if (!step1 && !step2) form.appendChild(mkCard());

    var hint = form.querySelector("[data-step-hint]");
    if (hint && !hint.querySelector("[data-jr-fl-inline]")) {
      var inline = document.createElement("a");
      inline.className = "jr-fl-inline";
      inline.setAttribute("data-jr-fl-inline", "1");
      inline.addEventListener("click", function () { saveFormDraft(form); });
      hint.appendChild(document.createElement("br"));
      hint.appendChild(inline);
    }

    // Capture phase on the form runs before the theme's submit handler builds FormData.
    form.addEventListener("submit", function () { appendReference(form); }, true);

    // The theme hides the form after a successful send: forget the reference then.
    if (window.MutationObserver) {
      new MutationObserver(function () {
        if (form.hidden) clearRef();
      }).observe(form, { attributes: true, attributeFilter: ["hidden"] });
    }
  }

  function init() {
    var params = new URLSearchParams(window.location.search);
    var returned = (params.get("furniture_list") || "").toUpperCase();
    if (REF_RE.test(returned)) {
      setRef(returned);
      var clean = new URL(window.location.href);
      clean.searchParams.delete("furniture_list");
      try { history.replaceState(null, "", clean.pathname + clean.search + "#quote"); } catch (e) {}
    }
    var forms = document.querySelectorAll("form[data-quote-form]");
    if (!forms.length) return;
    injectStyles();
    Array.prototype.forEach.call(forms, enhance);
    renderAll();

    if (REF_RE.test(returned)) {
      // Back from the list: once the theme has restored its draft, re-open the form at step 2.
      var form = forms[0];
      var waited = 0;
      (function reopen() {
        if (!form.classList.contains("is-enhanced") && waited < 6000) {
          waited += 100;
          setTimeout(reopen, 100);
          return;
        }
        setTimeout(function () {
          var nextBtn = form.querySelector("[data-step-next]");
          var step1Ready = Array.prototype.every.call(
            form.querySelectorAll('[data-step="1"] [required]'),
            function (el) { return !!el.value; }
          );
          if (nextBtn && step1Ready) nextBtn.click();
          var section = document.getElementById("quote") || form;
          section.scrollIntoView({ block: "start" });
        }, 50);
      })();
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { setTimeout(init, 0); });
  else setTimeout(init, 0);
})();
