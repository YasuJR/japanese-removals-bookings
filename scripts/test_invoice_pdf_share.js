#!/usr/bin/env node
"use strict";

var assert = require("assert");
var path = require("path");
var share = require(path.join(__dirname, "..", "static", "invoice_share.js"));

function fakePdfFile(name) {
  return new File([Buffer.from("%PDF-1.4 test")], name || "invoice-INV52.pdf", {
    type: "application/pdf",
  });
}

function stubDocument() {
  var originalDocument = global.document;
  var originalURL = global.URL;
  var clicked = { href: "", download: "", count: 0 };
  global.URL = {
    createObjectURL: function () {
      return "blob:invoice-file";
    },
    revokeObjectURL: function () {},
  };
  global.document = {
    createElement: function () {
      return {
        click: function () {
          clicked.count += 1;
        },
        href: "",
        download: "",
        rel: "",
        set download(value) {
          clicked.download = value;
        },
        get download() {
          return clicked.download;
        },
        set href(value) {
          clicked.href = value;
        },
        get href() {
          return clicked.href;
        },
      };
    },
    body: {
      appendChild: function () {},
      removeChild: function () {},
    },
  };
  return {
    clicked: clicked,
    restore: function () {
      global.document = originalDocument;
      global.URL = originalURL;
    },
  };
}

function test_share_data_contains_only_files() {
  var file = fakePdfFile();
  var data = share.buildShareData(file);
  assert.deepStrictEqual(Object.keys(data), ["files"]);
  assert.strictEqual(data.files.length, 1);
  assert.strictEqual(data.files[0], file);
  assert.strictEqual("url" in data, false);
  assert.strictEqual("text" in data, false);
  assert.strictEqual("title" in data, false);
}

function test_share_pdf_file_passes_files_only() {
  var file = fakePdfFile();
  var shared = null;
  var nav = {
    share: function (payload) {
      shared = payload;
      return Promise.resolve();
    },
  };
  return share.sharePdfFile(file, nav).then(function () {
    assert.deepStrictEqual(Object.keys(shared), ["files"]);
    assert.strictEqual(shared.files.length, 1);
    assert.strictEqual(shared.files[0], file);
    assert.strictEqual(shared.files[0].name, "invoice-INV52.pdf");
    assert.strictEqual(shared.url, undefined);
    assert.strictEqual(shared.text, undefined);
    assert.strictEqual(shared.title, undefined);
    assert.ok(!/https?:\/\//.test(JSON.stringify(shared)));
  });
}

function test_share_filename_from_disposition() {
  assert.strictEqual(
    share.filenameFromContentDisposition(
      'attachment; filename=invoice-INV52.pdf'
    ),
    "invoice-INV52.pdf"
  );
  assert.strictEqual(
    share.filenameFromContentDisposition(
      'attachment; filename="invoice-INV52.pdf"'
    ),
    "invoice-INV52.pdf"
  );
  var file = share.toPdfFile(
    new Blob(["%PDF"], { type: "application/pdf" }),
    "invoice-INV52.pdf"
  );
  assert.strictEqual(file.name, "invoice-INV52.pdf");
  assert.strictEqual(file.type, "application/pdf");
}

function test_can_share_requires_files_support() {
  var file = fakePdfFile();
  assert.strictEqual(share.canSharePdfFile(file, {}), false);
  assert.strictEqual(
    share.canSharePdfFile(file, {
      share: function () {},
      canShare: function (payload) {
        assert.deepStrictEqual(Object.keys(payload), ["files"]);
        return !!(payload.files && payload.files.length);
      },
    }),
    true
  );
  assert.strictEqual(
    share.canSharePdfFile(file, {
      share: function () {},
      canShare: function () {
        return false;
      },
    }),
    false
  );
}

function test_iphone_download_shares_files_only() {
  var file = fakePdfFile();
  var shared = null;
  var nav = {
    userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    share: function (payload) {
      shared = payload;
      return Promise.resolve();
    },
    canShare: function (payload) {
      return !!(payload && payload.files);
    },
  };
  return share.downloadOrShareOnApple(file, nav).then(function () {
    assert.ok(shared);
    assert.deepStrictEqual(Object.keys(shared), ["files"]);
    assert.strictEqual(shared.files[0].name, "invoice-INV52.pdf");
    assert.strictEqual(shared.url, undefined);
    assert.strictEqual(shared.text, undefined);
    assert.strictEqual(shared.title, undefined);
    assert.ok(String(shared.files[0].name).indexOf("http") === -1);
  });
}

function test_desktop_download_does_not_share() {
  var file = fakePdfFile();
  var shared = null;
  var stub = stubDocument();
  var nav = {
    userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0.0.0",
    share: function (payload) {
      shared = payload;
      return Promise.resolve();
    },
    canShare: function (payload) {
      return !!(payload && payload.files);
    },
  };
  return share
    .downloadOrShareOnApple(file, nav)
    .then(function () {
      assert.strictEqual(shared, null);
      assert.ok(stub.clicked.count >= 1);
      assert.strictEqual(stub.clicked.download, "invoice-INV52.pdf");
      assert.ok(!/^https?:/i.test(stub.clicked.href || ""));
    })
    .finally(function () {
      stub.restore();
    });
}

function test_share_or_download_uses_files_only() {
  var file = fakePdfFile();
  var shared = null;
  var nav = {
    share: function (payload) {
      shared = payload;
      return Promise.resolve();
    },
    canShare: function (payload) {
      return !!(payload && payload.files);
    },
  };
  return share.shareOrDownload(file, nav).then(function () {
    assert.ok(shared);
    assert.deepStrictEqual(Object.keys(shared), ["files"]);
    assert.strictEqual(shared.files[0].name, "invoice-INV52.pdf");
    assert.strictEqual(shared.url, undefined);
    assert.strictEqual(shared.text, undefined);
    assert.strictEqual(shared.title, undefined);
  });
}

function test_falls_back_when_file_share_unsupported() {
  var file = fakePdfFile();
  var stub = stubDocument();
  return share
    .shareOrDownload(file, {})
    .then(function () {
      assert.ok(stub.clicked.count >= 1);
    })
    .finally(function () {
      stub.restore();
    });
}

function test_is_pdf_response() {
  assert.strictEqual(
    share.isPdfResponse({
      headers: { get: function () { return "application/pdf"; } },
    }),
    true
  );
  assert.strictEqual(
    share.isPdfResponse({
      headers: { get: function () { return "text/html; charset=utf-8"; } },
    }),
    false
  );
}

function test_iphone_download_from_url_uses_disposition_name() {
  var originalFetch = global.fetch;
  var shared = null;
  global.fetch = function () {
    return Promise.resolve({
      ok: true,
      headers: {
        get: function (name) {
          if (String(name).toLowerCase() === "content-disposition") {
            return "attachment; filename=invoice-INV52.pdf";
          }
          if (String(name).toLowerCase() === "content-type") {
            return "application/pdf";
          }
          return "";
        },
      },
      blob: function () {
        return Promise.resolve(new Blob(["%PDF-1.4"], { type: "application/pdf" }));
      },
    });
  };
  var nav = {
    userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
    share: function (payload) {
      shared = payload;
      return Promise.resolve();
    },
    canShare: function (payload) {
      return !!(payload && payload.files);
    },
  };
  return share
    .downloadInvoicePdfFromUrl("/bookings/52/invoice.pdf", nav)
    .then(function () {
      assert.ok(shared);
      assert.deepStrictEqual(Object.keys(shared), ["files"]);
      assert.strictEqual(shared.files[0].name, "invoice-INV52.pdf");
      assert.strictEqual(shared.url, undefined);
      assert.strictEqual(shared.text, undefined);
      assert.ok(!/onrender\.com|Staff login|https?:\/\//i.test(JSON.stringify(shared)));
    })
    .finally(function () {
      global.fetch = originalFetch;
    });
}

function test_is_apple_touch_device() {
  assert.strictEqual(
    share.isAppleTouchDevice({
      userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
    }),
    true
  );
  assert.strictEqual(
    share.isAppleTouchDevice({
      userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
      platform: "MacIntel",
      maxTouchPoints: 0,
    }),
    false
  );
}

var tests = [
  test_share_data_contains_only_files,
  test_share_pdf_file_passes_files_only,
  test_share_filename_from_disposition,
  test_can_share_requires_files_support,
  test_iphone_download_shares_files_only,
  test_desktop_download_does_not_share,
  test_share_or_download_uses_files_only,
  test_falls_back_when_file_share_unsupported,
  test_is_pdf_response,
  test_iphone_download_from_url_uses_disposition_name,
  test_is_apple_touch_device,
];

var failed = 0;
var chain = Promise.resolve();
tests.forEach(function (fn) {
  chain = chain.then(function () {
    return Promise.resolve()
      .then(fn)
      .then(function () {
        console.log("PASS:", fn.name);
      })
      .catch(function (err) {
        failed += 1;
        console.log("FAIL:", fn.name, err && err.message ? err.message : err);
      });
  });
});

chain.then(function () {
  console.log("\n" + (tests.length - failed) + "/" + tests.length + " passed");
  process.exit(failed ? 1 : 0);
});
