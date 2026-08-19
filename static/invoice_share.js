(function (root) {
  var SHARE_FILENAME = "invoice.pdf";
  var PDF_TYPE = "application/pdf";

  function buildShareData(pdfFile) {
    return { files: [pdfFile] };
  }

  function canSharePdfFile(pdfFile, nav) {
    nav = nav || (typeof navigator !== "undefined" ? navigator : null);
    if (!nav || typeof nav.share !== "function") return false;
    if (typeof nav.canShare !== "function") return false;
    try {
      return !!nav.canShare(buildShareData(pdfFile));
    } catch (err) {
      return false;
    }
  }

  function downloadPdfFile(pdfFile) {
    var objectUrl = URL.createObjectURL(pdfFile);
    var link = document.createElement("a");
    link.href = objectUrl;
    link.download = SHARE_FILENAME;
    link.rel = "noopener";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(function () {
      URL.revokeObjectURL(objectUrl);
    }, 1500);
  }

  function toPdfFile(blob) {
    return new File([blob], SHARE_FILENAME, { type: PDF_TYPE });
  }

  function isPdfResponse(response) {
    var contentType = (response.headers.get("content-type") || "").toLowerCase();
    return contentType.indexOf("pdf") !== -1;
  }

  function shareOrDownload(pdfFile, nav) {
    nav = nav || (typeof navigator !== "undefined" ? navigator : null);
    if (canSharePdfFile(pdfFile, nav)) {
      return nav.share(buildShareData(pdfFile)).catch(function (err) {
        if (err && err.name === "AbortError") return;
        downloadPdfFile(pdfFile);
      });
    }
    downloadPdfFile(pdfFile);
    return Promise.resolve();
  }

  function fetchInvoicePdf(url) {
    return fetch(url, {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/pdf" },
    }).then(function (response) {
      if (!response.ok || !isPdfResponse(response)) {
        throw new Error("Invoice PDF is not available.");
      }
      return response.blob();
    });
  }

  function shareInvoicePdfFromUrl(url, nav) {
    return fetchInvoicePdf(url).then(function (blob) {
      return shareOrDownload(toPdfFile(blob), nav);
    });
  }

  function bindShareButtons() {
    if (typeof document === "undefined") return;
    document.querySelectorAll("[data-share-invoice-pdf]").forEach(function (button) {
      if (button.getAttribute("data-share-bound") === "1") return;
      button.setAttribute("data-share-bound", "1");
      button.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        var url = button.getAttribute("data-share-invoice-pdf") || "";
        if (!url || button.disabled) return;
        button.disabled = true;
        shareInvoicePdfFromUrl(url)
          .catch(function () {
            window.alert("Could not share the invoice PDF. Try Download PDF instead.");
          })
          .then(function () {
            button.disabled = false;
          });
      });
    });
  }

  var api = {
    SHARE_FILENAME: SHARE_FILENAME,
    buildShareData: buildShareData,
    canSharePdfFile: canSharePdfFile,
    downloadPdfFile: downloadPdfFile,
    toPdfFile: toPdfFile,
    isPdfResponse: isPdfResponse,
    shareOrDownload: shareOrDownload,
    fetchInvoicePdf: fetchInvoicePdf,
    shareInvoicePdfFromUrl: shareInvoicePdfFromUrl,
    bindShareButtons: bindShareButtons,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.JRInvoiceShare = api;
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", bindShareButtons);
    } else {
      bindShareButtons();
    }
  }
})(typeof window !== "undefined" ? window : this);
