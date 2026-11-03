"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const underscore = require("underscore");
const postImport = require("../js/util/post_import.js");

const postUrl = "https://www.furaffinity.net/view/123/";
const photoUrl = "https://d.furaffinity.net/art/uploader/123/123.uploader_original.png";
const videoUrl = "https://d.furaffinity.net/art/uploader/124/124.uploader_original.mp4";
const posterUrl = "https://t.furaffinity.net/123@400-123.jpg";
const jpeg = "data:image/jpeg;base64,/9j/2Q==";

function source(relativePath) {
    return fs.readFileSync(path.join(__dirname, relativePath), "utf8");
}

function metadata(updates = {}) {
    return Object.assign({
        provider: "furaffinity", postUrl, contentUrl: photoUrl, mimeType: "image/png",
        previewUrl: posterUrl,
        author: { username: "Uploader", url: "https://www.furaffinity.net/user/uploader/" },
        description: "Submission owner: [Uploader](https://www.furaffinity.net/user/uploader/)\n\n**Original text**",
        sources: [postUrl], safety: "safe", warnings: [],
        // FA's uncategorized keywords must never become inferred tag groups.
        tags: { artist: ["guessed_uploader"], character: ["guessed_character"], general: ["keyword"] },
        keywords: ["keyword"],
        media: [{ contentUrl: photoUrl, mimeType: "image/png", previewUrl: posterUrl, postUrl }],
    }, updates);
}

test("Fur Affinity drafts retain original media, credit, sources and rating without importing keywords", () => {
    const original = metadata();
    const unchanged = JSON.stringify(original);
    const [expanded] = postImport.expandImportMedia(original);
    const draft = postImport.createImportDraft(expanded);
    assert.equal(draft.url, photoUrl);
    assert.equal(draft.source, postUrl);
    assert.equal(draft.safety, "safe");
    assert.equal(draft.importMimeType, "image/png");
    assert.equal(draft.importPreviewUrl, posterUrl);
    assert.equal(draft.description, original.description);
    assert.equal(draft.importInfo.author.username, "Uploader");
    assert.deepEqual(draft.tags, []);
    assert.equal(draft.manualTags, "");
    assert.deepEqual(draft.importTagCategories, { artist: [], character: [] });
    assert.equal(JSON.stringify(original), unchanged);
});

test("Fur Affinity preview URLs must be public HTTPS thumbnail URLs without credentials or tokens", () => {
    assert.equal(postImport.createImportDraft(metadata()).importPreviewUrl, posterUrl);
    for (const previewUrl of [
        "https://t.furaffinity.net.evil.test/123.jpg",
        "https://sub.t.furaffinity.net/123.jpg",
        "https://d.furaffinity.net/123.jpg",
        "https://www.furaffinity.net/123.jpg",
        "http://t.furaffinity.net/123.jpg",
        "https://user:password@t.furaffinity.net/123.jpg",
        "https://t.furaffinity.net:444/123.jpg",
        "https://t.furaffinity.net/123.jpg?b=secret",
        "https://t.furaffinity.net/123.jpg#token",
        "https://t.furaffinity.net/123');color:red;.jpg",
        "https://t.furaffinity.net/\n123.jpg",
        "data:image/jpeg;base64,AA==",
    ]) {
        const draft = postImport.createImportDraft(metadata({ previewUrl }));
        assert.equal(draft.importPreviewUrl, null, previewUrl);
        assert.equal(draft.url, photoUrl);
    }
});

function rendering() {
    const misc = { module: { exports: {} }, require: () => ({}) };
    vm.runInNewContext(source("../js/util/misc.js"), misc);
    const helpers = {
        module: { exports: {} }, DOMParser: class {}, document: { addEventListener() {} },
        require(name) { return name === "./misc.js" ? misc.module.exports : {}; },
    };
    vm.runInNewContext(source("../js/util/views.js") + "\nmodule.exports.makeThumbnail = makeThumbnail;", helpers);
    const row = underscore.template(source("../html/post_upload_row.tpl"), { variable: "ctx" });
    const view = { module: { exports: {} }, URL, CustomEvent: class {}, require(name) {
        if (name === "../events.js") return { EventTarget: class { dispatchEvent() {} } };
        if (name === "../api.js") return { isLoggedIn: () => true };
        if (name === "../util/views.js") return { getTemplate: () => row };
        if (name === "../util/post_import.js") return postImport;
        return {};
    } };
    vm.runInNewContext(source("../js/views/post_upload_view.js"), view);
    return { View: view.module.exports, row, helpers: helpers.module.exports };
}

for (const [contentUrl, mimeType] of [[photoUrl, "image/png"], [videoUrl, "video/mp4"]]) {
    test("the real Fur Affinity upload row renders an original " + mimeType + " with its poster", () => {
        const { View, row, helpers } = rendering();
        const input = { value: "" };
        const view = Object.create(View.prototype);
        view._rootNode = { querySelector: () => input };
        let uploadable;
        view.addUploadables = items => { [uploadable] = items; };
        const original = metadata({
            contentUrl, mimeType, media: [{ contentUrl, mimeType, previewUrl: posterUrl, postUrl }],
        });
        view.addImportedPost(postImport.expandImportMedia(original)[0]);
        const html = row({ uploadable, enableSafety: false, canUploadAnonymously: false, makeThumbnail: helpers.makeThumbnail });
        const preview = html.split("<div class='uploadable'>")[0];
        assert.ok(preview.includes("href='" + contentUrl + "'"));
        assert.ok(preview.includes('src="' + posterUrl + '"'));
        assert.doesNotMatch(preview, /thumbnail empty|<video\b/);
        assert.equal(uploadable.mimeType, mimeType);
        assert.equal(uploadable.url, contentUrl);
        assert.equal(uploadable.description, original.description);
        assert.ok(html.includes(underscore.escape(original.description)));
        assert.equal(input.value, postUrl);
    });
}

function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    promise.aborted = false;
    promise.abort = () => { promise.aborted = true; reject(new Error("Aborted")); };
    return { promise, resolve, reject };
}

async function flush() {
    for (let index = 0; index < 8; index++) await Promise.resolve();
}

function controllerFixture() {
    const calls = [], previews = [deferred(), deferred()], messages = [];
    const sandbox = { module: { exports: {} }, require(name) {
        if (name === "../api.js") return { post(url, body) {
            const preview = previews[calls.length];
            calls.push({ url, body });
            return preview.promise;
        } };
        if (name === "../util/uri.js") return { formatApiLink: name => "/" + name };
        if (name === "../util/post_import.js") return postImport;
        return class {};
    } };
    vm.runInNewContext(source("../js/controllers/post_upload_controller.js") + "\nmodule.exports.Controller = PostUploadController;", sandbox);
    const controller = Object.create(sandbox.module.exports.Controller.prototype);
    const view = {
        active: true,
        hasUploadable(item) { return this.active && item.rowNode.isConnected; },
        setImportedPreview(item, url) { item.importPreviewUrl = url; },
        showInfo(message) { messages.push(message); },
    };
    Object.assign(controller, { _view: view, _previewRequests: new Set(), _importRequest: null });
    const draft = Object.assign({ rowNode: { isConnected: true } }, postImport.createImportDraft(metadata()));
    return { controller, view, draft, calls, previews, messages };
}

test("Fur Affinity server previews serialize with private providers and preserve pending edits", async () => {
    const fixture = controllerFixture();
    const first = fixture.controller._loadImportThumbnail(metadata(), fixture.draft);
    const secondDraft = Object.assign({}, fixture.draft, { rowNode: { isConnected: true } });
    const secondPost = "https://inkbunny.net/s/456-p1-";
    const second = fixture.controller._loadImportThumbnail({ provider: "inkbunny", postUrl: secondPost }, secondDraft);
    await flush();
    assert.equal(fixture.calls.length, 1);
    assert.equal(fixture.calls[0].url, "/import-thumbnail");
    assert.equal(fixture.calls[0].body.url, postUrl);
    Object.assign(fixture.draft, { description: "Pending edit", manualTags: "manual_tag", source: "https://source.example/edited", safety: "sketchy" });
    const originalUrl = fixture.draft.url;
    fixture.previews[0].resolve({ previewUrl: jpeg });
    await first;
    await flush();
    assert.equal(fixture.calls.length, 2);
    assert.equal(fixture.calls[1].body.url, secondPost);
    assert.equal(fixture.draft.importPreviewUrl, jpeg);
    assert.equal(fixture.draft.url, originalUrl);
    assert.equal(fixture.draft.description, "Pending edit");
    assert.equal(fixture.draft.manualTags, "manual_tag");
    assert.equal(fixture.draft.source, "https://source.example/edited");
    assert.equal(fixture.draft.safety, "sketchy");
    assert.deepEqual(fixture.messages, []);
    fixture.previews[1].resolve({ previewUrl: jpeg });
    await second;
});

test("leaving Fur Affinity import cancels active and queued preview work", async () => {
    const fixture = controllerFixture();
    const first = fixture.controller._loadImportThumbnail(metadata(), fixture.draft);
    const second = fixture.controller._loadImportThumbnail(metadata(), fixture.draft);
    await flush();
    fixture.view.active = false;
    fixture.controller._abortImportRequests();
    await Promise.all([first, second]);
    assert.equal(fixture.calls.length, 1);
    assert.equal(fixture.previews[0].promise.aborted, true);
    assert.equal(fixture.controller._previewRequests.size, 0);
    assert.equal(fixture.draft.importPreviewUrl, posterUrl);
    assert.deepEqual(fixture.messages, []);
});
