"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const underscore = require("underscore");
const postImport = require("../js/util/post_import.js");

const did = "did:plc:abcdefghijklmnopqrstuvwx";
const postUrl = "https://bsky.app/profile/" + did + "/post/3lfixturepost";
const pds = "https://example.us-east.host.bsky.network/xrpc/com.atproto.sync.getBlob?did=" + encodeURIComponent(did) + "&cid=";
const photoUrl = pds + "bafkreiphoto";
const secondPhotoUrl = pds + "bafkreisecondphoto";
const videoUrl = pds + "bafkreivideo";
const photoPreview = "https://cdn.bsky.app/img/feed_thumbnail/plain/" + did + "/bafkreiphoto@jpeg";
const videoPreview = "https://video.bsky.app/watch/" + encodeURIComponent(did) + "/bafkreivideo/thumbnail.jpg";
const jpeg = "data:image/jpeg;base64,/9j/2Q==";

function source(relativePath) {
    return fs.readFileSync(path.join(__dirname, relativePath), "utf8");
}

function metadata(updates = {}) {
    return Object.assign({
        provider: "bluesky", postUrl, contentUrl: photoUrl, mimeType: "image/jpeg",
        author: { username: "artist.example", url: "https://bsky.app/profile/" + did },
        description: "Author: [artist.example](https://bsky.app/profile/" + did + ")\n\nLiteral \\*post text\\* with &#35;hashtag and a [source](https://source.example/).",
        sources: [postUrl], safety: "unsafe", warnings: ["Review the rating before uploading."],
        // The client must not turn unclassified tags or rich-text facets into tags.
        tags: { artist: ["guessed_author"], character: ["guessed_character"], general: ["hashtag"] },
        facets: [{ features: [{ $type: "app.bsky.richtext.facet#tag", tag: "hashtag" }] }],
        media: [
            { contentUrl: photoUrl, mimeType: "image/jpeg", previewUrl: photoPreview, postUrl: postUrl + "#media-1" },
            { contentUrl: secondPhotoUrl, mimeType: "image/png", previewUrl: photoPreview, postUrl: postUrl + "#media-2" },
        ],
    }, updates);
}

test("Bluesky photos become ordered editable drafts with full blobs and no inferred tags or hashtags", () => {
    const original = metadata();
    const unchanged = JSON.stringify(original);
    const expanded = postImport.expandImportMedia(original);
    const drafts = expanded.map(postImport.createImportDraft);
    assert.deepEqual(drafts.map(draft => draft.url), [photoUrl, secondPhotoUrl]);
    assert.deepEqual(drafts.map(draft => draft.importMimeType), ["image/jpeg", "image/png"]);
    assert.deepEqual(drafts.map(draft => draft.source), [postUrl + "#media-1\n" + postUrl, postUrl + "#media-2\n" + postUrl]);
    for (const draft of drafts) {
        assert.deepEqual(draft.tags, []);
        assert.equal(draft.manualTags, "");
        assert.deepEqual(draft.importTagCategories, { artist: [], character: [] });
        assert.equal(draft.description, original.description);
        assert.equal(draft.safety, "unsafe");
        assert.equal(draft.importInfo.author.username, "artist.example");
        assert.deepEqual(draft.importInfo.warnings, original.warnings);
    }
    assert.equal(JSON.stringify(original), unchanged);
});

test("Bluesky previews are restricted to public HTTPS Bluesky media hosts without tokens", () => {
    for (const previewUrl of [photoPreview, videoPreview]) {
        assert.equal(postImport.createImportDraft(metadata({ previewUrl })).importPreviewUrl, previewUrl);
    }
    for (const previewUrl of [
        "https://cdn.bsky.app.evil.test/photo.jpg", "https://sub.cdn.bsky.app/photo.jpg",
        "https://video.bsky.app.evil.test/thumbnail.jpg", "https://bsky.app/photo.jpg",
        "http://cdn.bsky.app/photo.jpg", "https://user:password@cdn.bsky.app/photo.jpg",
        "https://video.bsky.app:444/thumbnail.jpg", "https://cdn.bsky.app/photo.jpg?token=secret",
        "https://cdn.bsky.app/photo.jpg#fragment", "https://cdn.bsky.app/photo');color:red;.jpg",
        "https://cdn.bsky.app/\nphoto.jpg", photoUrl, "data:image/jpeg;base64,AA==",
    ]) {
        const draft = postImport.createImportDraft(metadata({ previewUrl }));
        assert.equal(draft.importPreviewUrl, null, previewUrl);
        assert.equal(draft.url, photoUrl);
    }
});

test("an invalid later Bluesky media item rejects the whole draft batch", () => {
    for (const media of [[], [null], [metadata().media[0], { contentUrl: "" }]]) {
        assert.throws(() => postImport.expandImportMedia(metadata({ media })));
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

for (const [contentUrl, mimeType, previewUrl] of [
    [photoUrl, "image/jpeg", photoPreview],
    [videoUrl, "video/mp4", videoPreview],
]) {
    test("the real Bluesky upload row renders extensionless " + mimeType + " blobs with a separate poster", () => {
        const { View, row, helpers } = rendering();
        const input = { value: "" };
        const view = Object.create(View.prototype);
        view._rootNode = { querySelector: () => input };
        let uploadable;
        view.addUploadables = items => { [uploadable] = items; };
        const original = metadata({ media: [{ contentUrl, mimeType, previewUrl, postUrl: postUrl + "#media-2" }] });
        view.addImportedPost(postImport.expandImportMedia(original)[0]);
        const html = row({ uploadable, enableSafety: false, canUploadAnonymously: false, makeThumbnail: helpers.makeThumbnail });
        const preview = html.split("<div class='uploadable'>")[0];
        assert.ok(preview.includes("href='" + underscore.escape(contentUrl) + "'"));
        assert.ok(preview.includes('src="' + underscore.escape(previewUrl) + '"'));
        assert.doesNotMatch(preview, /thumbnail empty|<video\b|\.m3u8/);
        assert.equal(uploadable.mimeType, mimeType);
        assert.equal(uploadable.type, mimeType.startsWith("image/") ? "image" : "video");
        assert.equal(uploadable.url, contentUrl);
        assert.equal(uploadable.description, original.description);
        assert.ok(html.includes(underscore.escape(original.description)));
        assert.equal(input.value, postUrl);
    });
}

for (const [contentUrl, mimeType] of [[photoUrl, "image/jpeg"], [videoUrl, "video/mp4"]]) {
    test("Bluesky " + mimeType + " without a poster waits for the server preview without loading its original", () => {
        const { View, row, helpers } = rendering();
        const view = Object.create(View.prototype);
        view._rootNode = { querySelector: () => ({ value: "" }) };
        let uploadable;
        view.addUploadables = items => { [uploadable] = items; };
        const original = metadata({ media: [{ contentUrl, mimeType, postUrl: postUrl + "#media-1" }] });
        view.addImportedPost(postImport.expandImportMedia(original)[0]);
        const render = item => row({ uploadable: item, enableSafety: false, canUploadAnonymously: false, makeThumbnail: helpers.makeThumbnail });
        const placeholder = render(uploadable).split("<div class='uploadable'>")[0];
        assert.ok(placeholder.includes("href='" + underscore.escape(contentUrl) + "'"));
        assert.match(placeholder, /thumbnail empty/);
        assert.doesNotMatch(placeholder, /\bsrc=|background-image:|<video\b|<source\b/);
        assert.equal(uploadable.mimeType, mimeType);
        assert.equal(uploadable.url, contentUrl);
        assert.equal(uploadable.description, original.description);

        let updated;
        view.hasUploadable = item => item === uploadable;
        view._updateThumbnailNode = item => { updated = render(item); };
        view.setImportedPreview(uploadable, jpeg);
        const preview = updated.split("<div class='uploadable'>")[0];
        assert.ok(preview.includes('src="' + jpeg + '"'));
        assert.ok(preview.includes("href='" + underscore.escape(contentUrl) + "'"));
        assert.doesNotMatch(preview, /thumbnail empty|<video\b/);
        assert.equal(uploadable.url, contentUrl);
        assert.equal(uploadable.mimeType, mimeType);
        assert.equal(uploadable.description, original.description);
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
    const item = postImport.expandImportMedia(metadata())[0];
    const draft = Object.assign({ rowNode: { isConnected: true } }, postImport.createImportDraft(item));
    return { controller, view, draft, calls, previews, messages, item };
}

test("Bluesky page previews use normalized post selectors and preserve every pending edit", async () => {
    const fixture = controllerFixture();
    const first = fixture.controller._loadImportThumbnail(fixture.item, fixture.draft);
    const secondDraft = Object.assign({}, fixture.draft, { rowNode: { isConnected: true } });
    const second = fixture.controller._loadImportThumbnail({ provider: "bluesky", postUrl: postUrl + "#media-2" }, secondDraft);
    await flush();
    assert.equal(fixture.calls.length, 1);
    assert.deepEqual(Object.keys(fixture.calls[0].body), ["url"]);
    assert.equal(fixture.calls[0].body.url, postUrl + "#media-1");
    Object.assign(fixture.draft, { description: "Pending edit", manualTags: "manual_tag", source: "https://source.example/edited", safety: "sketchy" });
    fixture.previews[0].resolve({ previewUrl: jpeg });
    await first;
    await flush();
    assert.equal(fixture.calls.length, 2);
    assert.equal(fixture.calls[1].body.url, postUrl + "#media-2");
    assert.equal(fixture.draft.importPreviewUrl, jpeg);
    assert.equal(fixture.draft.url, photoUrl);
    assert.equal(fixture.draft.description, "Pending edit");
    assert.equal(fixture.draft.manualTags, "manual_tag");
    assert.equal(fixture.draft.source, "https://source.example/edited");
    assert.equal(fixture.draft.safety, "sketchy");
    assert.deepEqual(fixture.messages, []);
    fixture.previews[1].resolve({ previewUrl: jpeg });
    await second;
});

test("leaving Bluesky import cancels active and queued previews without stale field updates", async () => {
    const fixture = controllerFixture();
    const first = fixture.controller._loadImportThumbnail(fixture.item, fixture.draft);
    const second = fixture.controller._loadImportThumbnail(fixture.item, fixture.draft);
    await flush();
    fixture.view.active = false;
    fixture.controller._abortImportRequests();
    await Promise.all([first, second]);
    assert.equal(fixture.calls.length, 1);
    assert.equal(fixture.previews[0].promise.aborted, true);
    assert.equal(fixture.controller._previewRequests.size, 0);
    assert.equal(fixture.draft.importPreviewUrl, photoPreview);
    assert.deepEqual(fixture.messages, []);
});
