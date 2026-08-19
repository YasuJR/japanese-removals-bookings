#!/usr/bin/env node
"use strict";

var assert = require("assert");
var path = require("path");
var share = require(path.join(__dirname, "..", "static", "invoice_share.js"));

function fakePdfFile() {
  return new File([Buffer.from("%PDF-1.4 test")], "invoice.pdf", {
    type: "application/pdf",
  });
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

function test_share_filename_is_invoice_pdf() {
  assert.strictEqual(share.SHARE_FILENAME, "invoice.pdf");
  var file = share.toPdfFile(new Blob(["%PDF"], { type: "application/pdf" }));
  assert.strictEqual(file.name, "invoice.pdf");
  assert.strictEqual(file.type, "application/pdf");
}

function test_can_share_requires_files_support() {
  var file = fakePdfFile();
  assert.strictEqual(share.canSharePdfFile(file, {}), false);
  assert.strictEqual(
    share.canSharePdfFile(file, {
      share: function () {},
      canShare: function (payload) {
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
    assert.strictEqual(shared.files[0].name, "invoice.pdf");
    assert.strictEqual(shared.url, undefined);
    assert.strictEqual(shared.text, undefined);
    assert.strictEqual(shared.title, undefined);
  });
}

function test_falls_back_when_file_share_unsupported() {
  var file = fakePdfFile();
  var clicked = false;
  var originalDocument = global.document;
  global.document = {
    createElement: function () {
      return {
        click: function () {
          clicked = true;
        },
        href: "",
        download: "",
        rel: "",
      };
    },
    body: {
      appendChild: function () {},
      removeChild: function () {},
    },
  };
  return share
    .shareOrDownload(file, {})
    .then(function () {
      assert.strictEqual(clicked, true);
    })
    .finally(function () {
      global.document = originalDocument;
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

var tests = [
  test_share_data_contains_only_files,
  test_share_filename_is_invoice_pdf,
  test_can_share_requires_files_support,
  test_share_or_download_uses_files_only,
  test_falls_back_when_file_share_unsupported,
  test_is_pdf_response,
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
