"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const postImport = require("../js/util/post_import.js");

const postUrl = "https://x.com/test_author/status/123";
const photoUrl = "https://pbs.twimg.com/media/photo1?format=jpg&name=orig";
const videoUrl = "https://video.twimg.com/ext_tw_video/123/pu/vid/1280x720/video.mp4";
const posterUrl = "https://pbs.twimg.com/ext_tw_video_thumb/123/pu/img/poster.jpg";
const description = "Post author: [&#64;test\\_author](https://x.com/test_author)\n\nPost text.";

function metadata() {
    return {
        provider: "x", postUrl, contentUrl: photoUrl, mimeType: "image/jpeg",
        author: { username: "test_author", url: "https://x.com/test_author" },
        sources: [postUrl], description, safety: "unsafe", warnings: [],
        tags: { artist: [], character: [] },
        media: [
            { contentUrl: photoUrl, mimeType: "image/jpeg", postUrl: postUrl + "/photo/1" },
            { contentUrl: videoUrl, mimeType: "video/mp4", previewUrl: posterUrl, postUrl: postUrl + "/video/1" },
        ],
    };
}

test("X media expands to ordered editable drafts with shared author credit and original URLs", () => {
    const original = metadata();
    const unchanged = JSON.stringify(original);
    const expanded = postImport.expandImportMedia(original);
    assert.equal(expanded.length, 2);
    assert.deepEqual(expanded.map((item) => item.contentUrl), [photoUrl, videoUrl]);
    const drafts = expanded.map(postImport.createImportDraft);
    assert.deepEqual(drafts.map((item) => item.url), [photoUrl, videoUrl]);
    assert.equal(drafts[0].description, description);
    assert.equal(drafts[1].description, description);
    assert.equal(drafts[0].importMimeType, "image/jpeg");
    assert.equal(drafts[1].importMimeType, "video/mp4");
    assert.equal(drafts[0].importPreviewUrl, null);
    assert.equal(drafts[1].importPreviewUrl, posterUrl);
    assert.equal(drafts[0].source, postUrl + "/photo/1\n" + postUrl);
    assert.equal(drafts[1].source, postUrl + "/video/1\n" + postUrl);
    assert.equal(JSON.stringify(original), unchanged);
});

test("X usernames and hashtags are never assigned artist or character tags", () => {
    const original = metadata();
    original.tags = {
        artist: ["test_author"], character: ["hashtag_character"],
        general: ["hashtag_general"],
    };
    const draft = postImport.createImportDraft(original);
    assert.deepEqual(draft.tags, []);
    assert.equal(draft.manualTags, "");
    assert.deepEqual(draft.importTagCategories, { artist: [], character: [] });
    assert.equal(draft.description, description);
    assert.equal(draft.importInfo.author.username, "test_author");
});

test("X GIF videos retain only the loop flag through media expansion and draft creation", () => {
    const original = metadata();
    original.flags = ["loop", "sound"];
    original.media[0].flags = ["sound", "unrecognized"];
    original.media[1].flags = ["loop", "sound", "unrecognized"];
    const drafts = postImport.expandImportMedia(original).map(postImport.createImportDraft);
    assert.deepEqual(drafts[0].flags, []);
    assert.deepEqual(drafts[1].flags, ["loop"]);
    assert.equal(drafts[1].url, videoUrl);
    assert.equal(drafts[1].importMimeType, "video/mp4");
    assert.equal(Object.hasOwn(postImport.createImportDraft(Object.assign(metadata(), {
        provider: "gelbooru", flags: ["loop"],
    })), "flags"), false);
});

test("explicit photo selection retains its supplied index rather than selecting the first photo", () => {
    const original = metadata();
    original.postUrl = postUrl + "/photo/3";
    original.media = [{
        contentUrl: "https://pbs.twimg.com/media/photo3?format=png&name=orig",
        mimeType: "image/png", postUrl: original.postUrl,
    }];
    const [selected] = postImport.expandImportMedia(original);
    assert.equal(selected.contentUrl, original.media[0].contentUrl);
    assert.equal(selected.postUrl, postUrl + "/photo/3");
    assert.equal(selected.importPostUrl, original.postUrl);
});

test("other providers retain their existing single-draft behavior", () => {
    const original = Object.assign(metadata(), { provider: "gelbooru" });
    assert.deepEqual(postImport.expandImportMedia(original), [original]);
    assert.equal(postImport.expandImportMedia(original)[0], original);
});

test("invalid X media is rejected before any partial drafts are created", () => {
    for (const media of [[], [null], [{ contentUrl: "" }]]) {
        assert.throws(() => postImport.expandImportMedia(Object.assign(metadata(), { media })));
    }
});

test("X video posters are restricted to HTTPS Twitter image media while original URLs stay unchanged", () => {
    for (const previewUrl of [
        "https://pbs.twimg.com.evil.test/poster.jpg", "http://pbs.twimg.com/poster.jpg",
        "javascript:alert(1)", "https://user:password@pbs.twimg.com/poster.jpg",
        "https://pbs.twimg.com:444/poster.jpg",
    ]) {
        const draft = postImport.createImportDraft(Object.assign(metadata(), {
            contentUrl: videoUrl, mimeType: "video/mp4", previewUrl,
        }));
        assert.equal(draft.importPreviewUrl, null);
        assert.equal(draft.url, videoUrl);
    }
});

function loadView() {
    const sandbox = {
        module: { exports: {} }, URL, CustomEvent: class {},
        require(name) {
            if (name === "../events.js") return { EventTarget: class { dispatchEvent() {} } };
            if (name === "../api.js") return { isLoggedIn: () => true };
            if (name === "../util/views.js") return { getTemplate: () => () => "" };
            if (name === "../util/post_import.js") return postImport;
            return {};
        },
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../js/views/post_upload_view.js"), "utf8"), sandbox);
    return sandbox.module.exports;
}

test("query-format photos preview as images and batch creation keeps the normalized post-link input", () => {
    const View = loadView();
    const input = { value: "" }, added = [];
    const view = Object.create(View.prototype);
    view._rootNode = { querySelector: () => input };
    view.addUploadables = (items) => added.push(...items);
    for (const item of postImport.expandImportMedia(metadata())) view.addImportedPost(item);
    assert.equal(added.length, 2);
    assert.equal(input.value, postUrl);
    assert.equal(added[0].mimeType, "image/jpeg");
    assert.equal(added[0].type, "image");
    assert.equal(added[0].previewUrl, photoUrl);
    assert.equal(added[1].mimeType, "video/mp4");
    assert.equal(added[1].type, "video");
    assert.equal(added[1].previewUrl, posterUrl);
    assert.equal(added[1].key, videoUrl);
    assert.equal(added[1].url, videoUrl);
});

test("one X import request prepares every media draft without uploading or calling the Gelbooru preview endpoint", async () => {
    const calls = [], added = [], busy = [];
    const sandbox = {
        module: { exports: {} },
        require(name) {
            if (name === "../api.js") return { post: async (url, body) => {
                calls.push({ url, body });
                return metadata();
            } };
            if (name === "../util/uri.js") return { formatApiLink: (...parts) => "/" + parts.join("/") };
            if (name === "../util/post_import.js") return postImport;
            return class {};
        },
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../js/controllers/post_upload_controller.js"), "utf8") +
        "\nmodule.exports.Controller = PostUploadController;", sandbox);
    const controller = Object.create(sandbox.module.exports.Controller.prototype);
    controller._importRequest = null;
    controller._previewRequests = new Set();
    controller._view = {
        active: true, setImporting: (value) => busy.push(value), clearImportMessages() {},
        addImportedPost: (value) => { const draft = postImport.createImportDraft(value); added.push(draft); return draft; },
        showImportError: (message) => assert.fail(message),
    };
    controller._evtImport({ detail: { url: postUrl + "?s=20" } });
    for (let i = 0; i < 8; i++) await Promise.resolve();
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "/import-preview");
    assert.equal(added.length, 2);
    assert.deepEqual(added.map((item) => item.url), [photoUrl, videoUrl]);
    assert.deepEqual(busy, [true, false]);
    assert.equal(controller._previewRequests.size, 0);
});
