(function (root) {
  var SHARE_FILENAME = "invoice.pdf";
  var PDF_TYPE = "application/pdf";

  function buildShareData(pdfFile) {
    return { files: [pdfFile] };
  }

  function sharePdfFile(pdfFile, nav) {
    nav = nav || (typeof navigator !== "undefined" ? navigator : null);
    if (!nav || typeof nav.share !== "function") {
      return Promise.reject(new Error("Web Share API is not available."));
    }
    return nav.share({ files: [pdfFile] });
  }

  function canSharePdfFile(pdfFile, nav) {
    nav = nav || (typeof navigator !== "undefined" ? navigator : null);
    if (!nav || typeof nav.share !== "function") return false;
    if (typeof nav.canShare !== "function") return false;
    try {
      return !!nav.canShare({ files: [pdfFile] });
    } catch (err) {
      return false;
    }
  }

  function isAppleTouchDevice(nav) {
    nav = nav || (typeof navigator !== "undefined" ? navigator : null);
    if (!nav) return false;
    var ua = nav.userAgent || "";
    if (/iPad|iPhone|iPod/.test(ua)) return true;
    return nav.platform === "MacIntel" && nav.maxTouchPoints > 1;
  }

  function filenameFromContentDisposition(header) {
    var value = header || "";
    var starred = /filename\*=(?:UTF-8''|)([^;]+)/i.exec(value);
    if (starred) {
      try {
        return decodeURIComponent(starred[1].trim().replace(/^["']|["']$/g, ""));
      } catch (err) {
        /* use fallback below */
      }
    }
    var plain = /filename=([^;]+)/i.exec(value);
    if (!plain) return "";
    return plain[1].trim().replace(/^["']|["']$/g, "");
  }

  function downloadPdfFile(pdfFile) {
    var filename = (pdfFile && pdfFile.name) || SHARE_FILENAME;
    var objectUrl = URL.createObjectURL(pdfFile);
    var link = document.createElement("a");
    link.href = objectUrl;
    link.download = filename;
    link.rel = "noopener";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(function () {
      URL.revokeObjectURL(objectUrl);
    }, 1500);
  }

  function toPdfFile(blob, filename) {
    return new File([blob], filename || SHARE_FILENAME, { type: PDF_TYPE });
  }

  function isPdfResponse(response) {
    var contentType = (response.headers.get("content-type") || "").toLowerCase();
    return contentType.indexOf("pdf") !== -1;
  }

  function shareOrDownload(pdfFile, nav) {
    nav = nav || (typeof navigator !== "undefined" ? navigator : null);
    if (canSharePdfFile(pdfFile, nav)) {
      return sharePdfFile(pdfFile, nav).catch(function (err) {
        if (err && err.name === "AbortError") return;
        downloadPdfFile(pdfFile);
      });
    }
    downloadPdfFile(pdfFile);
    return Promise.resolve();
  }

  function downloadOrShareOnApple(pdfFile, nav) {
    nav = nav || (typeof navigator !== "undefined" ? navigator : null);
    if (isAppleTouchDevice(nav) && canSharePdfFile(pdfFile, nav)) {
      return sharePdfFile(pdfFile, nav).catch(function (err) {
        if (err && err.name === "AbortError") return;
        downloadPdfFile(pdfFile);
      });
    }
    downloadPdfFile(pdfFile);
    return Promise.resolve();
  }

  function fetchInvoicePdfFile(pdfUrl) {
    return fetch(pdfUrl, {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/pdf" },
    }).then(function (response) {
      if (!response.ok || !isPdfResponse(response)) {
        throw new Error("Invoice PDF is not available.");
      }
      var filename =
        filenameFromContentDisposition(response.headers.get("content-disposition")) ||
        SHARE_FILENAME;
      return response.blob().then(function (blob) {
        return toPdfFile(blob, filename);
      });
    });
  }

  function fetchInvoicePdf(pdfUrl) {
    return fetchInvoicePdfFile(pdfUrl).then(function (pdfFile) {
      return pdfFile;
    });
  }

  function shareInvoicePdfFromUrl(pdfUrl, nav) {
    return fetchInvoicePdfFile(pdfUrl).then(function (pdfFile) {
      return shareOrDownload(pdfFile, nav);
    });
  }

  function downloadInvoicePdfFromUrl(pdfUrl, nav) {
    return fetchInvoicePdfFile(pdfUrl).then(function (pdfFile) {
      return downloadOrShareOnApple(pdfFile, nav);
    });
  }

  function bindAction(selector, attrName, boundFlag, handler) {
    if (typeof document === "undefined") return;
    document.querySelectorAll(selector).forEach(function (element) {
      if (element.getAttribute(boundFlag) === "1") return;
      element.setAttribute(boundFlag, "1");
      element.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        var pdfUrl = element.getAttribute(attrName) || "";
        if (!pdfUrl || element.disabled) return;
        element.disabled = true;
        handler(pdfUrl)
          .catch(function () {
            window.alert("Could not prepare the invoice PDF. Try again.");
          })
          .then(function () {
            element.disabled = false;
          });
      });
    });
  }

  function bindShareButtons() {
    bindAction(
      "[data-share-invoice-pdf]",
      "data-share-invoice-pdf",
      "data-share-bound",
      function (pdfUrl) {
        return shareInvoicePdfFromUrl(pdfUrl);
      }
    );
  }

  function bindDownloadButtons() {
    bindAction(
      "[data-download-invoice-pdf]",
      "data-download-invoice-pdf",
      "data-download-bound",
      function (pdfUrl) {
        return downloadInvoicePdfFromUrl(pdfUrl);
      }
    );
  }

  function bindInvoicePdfActions() {
    bindShareButtons();
    bindDownloadButtons();
  }

  var api = {
    SHARE_FILENAME: SHARE_FILENAME,
    buildShareData: buildShareData,
    sharePdfFile: sharePdfFile,
    canSharePdfFile: canSharePdfFile,
    isAppleTouchDevice: isAppleTouchDevice,
    filenameFromContentDisposition: filenameFromContentDisposition,
    downloadPdfFile: downloadPdfFile,
    toPdfFile: toPdfFile,
    isPdfResponse: isPdfResponse,
    shareOrDownload: shareOrDownload,
    downloadOrShareOnApple: downloadOrShareOnApple,
    fetchInvoicePdf: fetchInvoicePdf,
    fetchInvoicePdfFile: fetchInvoicePdfFile,
    shareInvoicePdfFromUrl: shareInvoicePdfFromUrl,
    downloadInvoicePdfFromUrl: downloadInvoicePdfFromUrl,
    bindShareButtons: bindShareButtons,
    bindDownloadButtons: bindDownloadButtons,
    bindInvoicePdfActions: bindInvoicePdfActions,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.JRInvoiceShare = api;
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", bindInvoicePdfActions);
    } else {
      bindInvoicePdfActions();
    }
  }
})(typeof window !== "undefined" ? window : this);
