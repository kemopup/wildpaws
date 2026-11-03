"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const postImport = require("../js/util/post_import.js");

const previewUrl = "data:image/jpeg;base64,/9j/2Q==";
const metadata = {
    provider: "gelbooru",
    postUrl: "https://gelbooru.com/index.php?page=post&s=view&id=123",
    contentUrl: "https://img4.gelbooru.com/images/original.mp4",
    tags: { artist: ["artist_name"], character: [] },
    sources: [], safety: "safe", description: "Artist description",
};

function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    promise.aborted = false;
    promise.abort = () => { promise.aborted = true; reject(new Error("Aborted")); };
    return { promise, resolve, reject };
}

async function flush() {
    // Drain both the preview result and the following cleanup callbacks.
    for (let i = 0; i < 8; i++) await Promise.resolve();
}

function loadController(api) {
    const sandbox = {
        module: { exports: {} },
        require(name) {
            if (name === "../api.js") return api;
            if (name === "../util/uri.js") return { formatApiLink: (...parts) => "/" + parts.join("/") };
            if (name === "../util/post_import.js") return postImport;
            return class {};
        },
    };
    vm.runInNewContext(
        fs.readFileSync(path.join(__dirname, "../js/controllers/post_upload_controller.js"), "utf8") +
        "\nmodule.exports.Controller = PostUploadController;", sandbox
    );
    return sandbox.module.exports.Controller;
}

function makeControllerFixture() {
    const imports = deferred(), preview = deferred(), calls = [], info = [], busy = [];
    const uploadable = { url: metadata.contentUrl, rowNode: { isConnected: true } };
    const Controller = loadController({ post(url, body) {
        calls.push({ url, body });
        return url === "/import-preview" ? imports.promise : preview.promise;
    } });
    const controller = Object.create(Controller.prototype);
    const view = {
        active: true,
        setImporting(value) { busy.push(value); }, clearImportMessages() {},
        addImportedPost(value) { Object.assign(uploadable, postImport.createImportDraft(value)); return uploadable; },
        hasUploadable(value) { return this.active && value.rowNode.isConnected; },
        setImportedPreview(value, url) { value.importPreviewUrl = url; },
        showInfo(message) { info.push(message); },
        showImportError(message) { assert.fail(message); },
    };
    Object.assign(controller, { _view: view, _importRequest: null, _previewRequests: new Set() });
    return { controller, view, imports, preview, uploadable, calls, info, busy };
}

test("metadata import is ready for upload while the independent JPEG preview is pending", async () => {
    const fixture = makeControllerFixture();
    fixture.controller._evtImport({ detail: { url: metadata.postUrl } });
    fixture.imports.resolve(metadata);
    await flush();
    assert.equal(fixture.controller._importRequest, null);
    assert.deepEqual(fixture.busy, [true, false]);
    assert.equal(fixture.controller._previewRequests.size, 1);
    assert.equal(fixture.calls[1].url, "/import-thumbnail");
    assert.equal(fixture.calls[1].body.url, metadata.postUrl);
    fixture.uploadable.description = "Pending user edit";
    fixture.preview.resolve({ previewUrl });
    await flush();
    assert.equal(fixture.uploadable.importPreviewUrl, previewUrl);
    assert.equal(fixture.uploadable.url, metadata.contentUrl);
    assert.equal(fixture.uploadable.description, "Pending user edit");
    assert.equal(fixture.controller._previewRequests.size, 0);
});

test("a late preview response cannot update a removed upload row", async () => {
    const fixture = makeControllerFixture();
    fixture.controller._loadImportThumbnail(metadata, fixture.uploadable);
    fixture.uploadable.rowNode.isConnected = false;
    fixture.preview.resolve({ previewUrl });
    await flush();
    assert.equal(fixture.uploadable.importPreviewUrl, undefined);
    assert.equal(fixture.controller._previewRequests.size, 0);
    assert.deepEqual(fixture.info, []);
});

test("preview failure leaves the original upload available and reports a nonblocking message", async () => {
    const fixture = makeControllerFixture();
    fixture.controller._loadImportThumbnail(metadata, fixture.uploadable);
    fixture.preview.reject(new Error("Provider unavailable"));
    await flush();
    assert.equal(fixture.uploadable.url, metadata.contentUrl);
    assert.deepEqual(fixture.busy, []);
    assert.match(fixture.info[0], /still upload/);
    assert.equal(fixture.controller._previewRequests.size, 0);
});

test("leaving the page aborts metadata and thumbnail requests without stale messages", async () => {
    const fixture = makeControllerFixture();
    fixture.controller._importRequest = fixture.imports.promise;
    fixture.imports.promise.catch(() => {});
    fixture.controller._loadImportThumbnail(metadata, fixture.uploadable);
    fixture.view.active = false;
    fixture.controller._abortImportRequests();
    await flush();
    assert.equal(fixture.imports.promise.aborted, true);
    assert.equal(fixture.preview.promise.aborted, true);
    assert.equal(fixture.controller._previewRequests.size, 0);
    assert.deepEqual(fixture.info, []);
});

test("other providers keep their existing browser preview behavior", () => {
    const fixture = makeControllerFixture();
    for (const provider of ["e621", "e6ai", "rule34", "tbib", "x"]) {
        fixture.controller._loadImportThumbnail({ ...metadata, provider }, fixture.uploadable);
    }
    assert.deepEqual(fixture.calls, []);
});

function makeInkbunnyFixture() {
    const imports = deferred();
    const previews = [deferred(), deferred(), deferred()];
    const calls = [], info = [], busy = [], uploadables = [];
    const postUrl = "https://inkbunny.net/s/123";
    const imported = {
        provider: "inkbunny", postUrl, sources: [postUrl],
        description: "Submission owner credit\n\nImported description", safety: "safe",
        tags: { artist: [], character: [] }, warnings: [],
        media: previews.map((_item, index) => ({
            contentUrl: "https://tx.ib.metapix.net/files/full/" + index + "_original.png",
            mimeType: "image/png", postUrl: postUrl + "-p" + (index + 1) + "-",
        })),
    };
    let nextPreview = 0;
    const Controller = loadController({ post(url, body) {
        calls.push({ url, body });
        return url === "/import-preview" ? imports.promise : previews[nextPreview++].promise;
    } });
    const controller = Object.create(Controller.prototype);
    const view = {
        active: true,
        setImporting(value) { busy.push(value); }, clearImportMessages() {},
        addImportedPost(value) {
            const uploadable = Object.assign({ rowNode: { isConnected: true } }, postImport.createImportDraft(value));
            uploadables.push(uploadable);
            return uploadable;
        },
        hasUploadable(value) { return this.active && value.rowNode.isConnected; },
        setImportedPreview(value, url) { value.importPreviewUrl = url; },
        showInfo(message) { info.push(message); },
        showImportError(message) { assert.fail(message); },
    };
    Object.assign(controller, { _view: view, _importRequest: null, _previewRequests: new Set() });
    return { controller, view, imports, previews, calls, info, busy, uploadables, imported };
}

async function startInkbunnyImport(fixture) {
    fixture.controller._evtImport({ detail: { url: fixture.imported.postUrl } });
    fixture.imports.resolve(fixture.imported);
    await flush();
}

test("Inkbunny page previews run in order without blocking upload or overwriting edited fields", async () => {
    const fixture = makeInkbunnyFixture();
    await startInkbunnyImport(fixture);
    assert.equal(fixture.uploadables.length, 3);
    assert.equal(fixture.controller._importRequest, null);
    assert.deepEqual(fixture.busy, [true, false]);
    assert.equal(fixture.calls.length, 2);
    assert.equal(fixture.calls[1].body.url, "https://inkbunny.net/s/123-p1-");
    assert.equal(fixture.controller._previewRequests.size, 1);

    const first = fixture.uploadables[0];
    Object.assign(first, { description: "Edited description", manualTags: "manual tag", tags: ["manual", "tag"],
        source: "https://source.example/edited", safety: "sketchy", anonymous: true });
    const edited = JSON.stringify(first);
    for (let index = 0; index < 3; index++) {
        fixture.previews[index].resolve({ previewUrl });
        await flush();
        assert.equal(fixture.uploadables[index].importPreviewUrl, previewUrl);
        if (index < 2) {
            assert.equal(fixture.calls.length, index + 3);
            assert.equal(fixture.calls[index + 2].body.url, "https://inkbunny.net/s/123-p" + (index + 2) + "-");
            assert.equal(fixture.controller._previewRequests.size, 1);
        }
    }
    assert.equal(fixture.controller._previewRequests.size, 0);
    assert.deepEqual({ ...first, importPreviewUrl: null }, JSON.parse(edited));
    assert.deepEqual(fixture.info, []);
});

test("removed Inkbunny pages are skipped and their late preview cannot overwrite anything", async () => {
    const fixture = makeInkbunnyFixture();
    await startInkbunnyImport(fixture);
    fixture.uploadables[0].rowNode.isConnected = false;
    fixture.uploadables[1].rowNode.isConnected = false;
    fixture.previews[0].resolve({ previewUrl });
    await flush();
    assert.equal(fixture.uploadables[0].importPreviewUrl, null);
    assert.equal(fixture.uploadables[1].importPreviewUrl, null);
    assert.equal(fixture.calls.length, 3);
    assert.equal(fixture.calls[2].body.url, "https://inkbunny.net/s/123-p3-");
    fixture.previews[1].resolve({ previewUrl });
    await flush();
    assert.equal(fixture.uploadables[2].importPreviewUrl, previewUrl);
    assert.deepEqual(fixture.info, []);
});

test("leaving Inkbunny upload aborts the active preview and prevents queued requests or messages", async () => {
    const fixture = makeInkbunnyFixture();
    await startInkbunnyImport(fixture);
    fixture.view.active = false;
    fixture.controller._abortImportRequests();
    await flush();
    await fixture.controller._inkbunnyPreviewQueue;
    assert.equal(fixture.previews[0].promise.aborted, true);
    assert.equal(fixture.calls.length, 2);
    assert.equal(fixture.controller._previewRequests.size, 0);
    assert.ok(fixture.uploadables.every(item => item.importPreviewUrl === null));
    assert.deepEqual(fixture.info, []);
});

test("a queued Inkbunny preview does not start after its view becomes inactive", async () => {
    const fixture = makeInkbunnyFixture();
    const item = fixture.imported.media[0];
    const uploadable = fixture.view.addImportedPost(Object.assign({}, fixture.imported, item));
    fixture.controller._loadImportThumbnail({ provider: "inkbunny", postUrl: item.postUrl }, uploadable);
    fixture.view.active = false;
    await fixture.controller._inkbunnyPreviewQueue;
    assert.deepEqual(fixture.calls, []);
});

test("an unavailable Inkbunny preview reports once and continues with the next page", async () => {
    const fixture = makeInkbunnyFixture();
    await startInkbunnyImport(fixture);
    fixture.previews[0].reject(new Error("Provider unavailable"));
    await flush();
    assert.equal(fixture.calls.length, 3);
    assert.equal(fixture.calls[2].body.url, "https://inkbunny.net/s/123-p2-");
    assert.equal(fixture.info.length, 1);
    assert.match(fixture.info[0], /still upload/);
    fixture.view.active = false;
    fixture.controller._abortImportRequests();
    await fixture.controller._inkbunnyPreviewQueue;
    assert.equal(fixture.info.length, 1);
});

test("preview replacement touches only the thumbnail wrapper and keeps original video URL", () => {
    const replacements = [], contexts = [], fields = { description: "Pending edit", tags: "manual tag" };
    const wrapper = { id: "existing-thumbnail" };
    const sandbox = {
        module: { exports: {} }, CustomEvent: class {},
        require(name) {
            if (name === "../api.js") return { isLoggedIn: () => true };
            if (name === "../events.js") return { EventTarget: class { dispatchEvent() {} } };
            if (name === "../util/post_import.js") return postImport;
            if (name === "../util/views.js") return {
                getTemplate: () => (ctx) => { contexts.push(ctx); return { querySelector: () => ({ childNodes: [ctx.uploadable.previewUrl] }) }; },
                replaceContent: (...args) => replacements.push(args),
            };
            return class {};
        },
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../js/views/post_upload_view.js"), "utf8"), sandbox);
    const view = Object.create(sandbox.module.exports.prototype);
    view._rootNode = { isConnected: true, querySelector: () => ({ value: "" }) };
    view._ctx = {};
    view._uploadables = [];
    view.addUploadables = (items) => {
        for (const item of items) {
            item.rowNode = { isConnected: true, fields, querySelector(selector) {
                assert.equal(selector, ".thumbnail-wrapper"); return wrapper;
            } };
            view._uploadables.push(item);
        }
    };
    const uploadable = view.addImportedPost(metadata);
    const originalKey = uploadable.key;
    view.setImportedPreview(uploadable, previewUrl);
    assert.equal(uploadable.url, metadata.contentUrl);
    assert.equal(uploadable.key, originalKey);
    assert.equal(uploadable.mimeType, "video/mp4");
    assert.equal(uploadable.previewUrl, previewUrl);
    assert.deepEqual(fields, { description: "Pending edit", tags: "manual tag" });
    assert.equal(replacements.length, 1);
    assert.equal(replacements[0][0], wrapper);
    assert.equal(contexts[0].uploadable, uploadable);
    uploadable.rowNode.isConnected = false;
    view.setImportedPreview(uploadable, "data:image/jpeg;base64,AAAA");
    assert.equal(replacements.length, 1);
    assert.equal(uploadable.importPreviewUrl, previewUrl);
});
