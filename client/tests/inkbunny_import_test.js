"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const postImport = require("../js/util/post_import.js");

const postUrl = "https://inkbunny.net/s/123";
const photoUrl = "https://tx.ib.metapix.net/files/full/123_original.png";
const videoUrl = "https://tx.ib.metapix.net/files/full/124_original.mp4";
const previewUrl = "https://tx.ib.metapix.net/thumbnails/large/124_original.jpg";

function metadata() {
    return {
        provider: "inkbunny", postUrl, contentUrl: photoUrl, mimeType: "image/png",
        author: { username: "Uploader", url: "https://inkbunny.net/Uploader" },
        sources: [postUrl], description: "Uploader credit\n\n**Submission description**",
        safety: "sketchy", warnings: ["Uncategorized keywords were not imported."],
        // The API cannot reliably classify these; never trust inferred categories.
        tags: { artist: ["Uploader"], character: ["guess"], general: ["keyword"] },
        keywords: ["keyword"],
        media: [
            { contentUrl: photoUrl, mimeType: "image/png", postUrl: postUrl + "-p1-" },
            { contentUrl: videoUrl, mimeType: "video/mp4", previewUrl, postUrl: postUrl + "-p2-" },
        ],
    };
}

test("Inkbunny pages become ordered drafts preserving originals and uploader credit without importing keywords", () => {
    const original = metadata();
    const unchanged = JSON.stringify(original);
    const expanded = postImport.expandImportMedia(original);
    const drafts = expanded.map(postImport.createImportDraft);
    assert.deepEqual(drafts.map(item => item.url), [photoUrl, videoUrl]);
    assert.deepEqual(drafts.map(item => item.importMimeType), ["image/png", "video/mp4"]);
    for (const draft of drafts) {
        assert.deepEqual(draft.tags, []);
        assert.equal(draft.manualTags, "");
        assert.deepEqual(draft.importTagCategories, { artist: [], character: [] });
        assert.equal(draft.description, original.description);
        assert.equal(draft.safety, "sketchy");
        assert.equal(draft.importInfo.author.username, "Uploader");
        assert.deepEqual(draft.importInfo.warnings, original.warnings);
    }
    assert.equal(drafts[0].source, postUrl + "-p1-\n" + postUrl);
    assert.equal(drafts[1].source, postUrl + "-p2-\n" + postUrl);
    assert.equal(drafts[1].importPreviewUrl, previewUrl);
    assert.equal(JSON.stringify(original), unchanged);
});

test("Inkbunny only accepts display previews from HTTPS Inkbunny and its media CDN", () => {
    for (const url of [
        previewUrl,
        "https://inkbunny.net/thumbnails/large/123.jpg",
        "https://cdn.inkbunny.net/thumbnails/large/123.jpg",
        "https://ib.metapix.net/thumbnails/large/123.jpg",
    ]) {
        assert.equal(postImport.createImportDraft(Object.assign(metadata(), { previewUrl: url })).importPreviewUrl, url);
    }
    for (const url of [
        "https://inkbunny.net.evil.test/123.jpg",
        "https://ib.metapix.net.evil.test/123.jpg",
        "https://metapix.net/123.jpg", "https://pbs.twimg.com/123.jpg",
        "http://tx.ib.metapix.net/123.jpg", "data:image/jpeg;base64,AA==",
        "https://user:password@tx.ib.metapix.net/123.jpg",
        "https://tx.ib.metapix.net:444/123.jpg",
        "https://tx.ib.metapix.net/private_thumbnails/123.jpg?sid=secret",
        "https://tx.ib.metapix.net/123.jpg#sid=secret",
        "https://tx.ib.metapix.net/123');color:red;.jpg",
        "https://tx.ib.metapix.net/\n123.jpg",
    ]) {
        const draft = postImport.createImportDraft(Object.assign(metadata(), { previewUrl: url }));
        assert.equal(draft.importPreviewUrl, null, url);
        assert.equal(draft.url, photoUrl);
    }
});

test("Inkbunny validates the entire media list before creating any partial drafts", () => {
    for (const media of [[], [null], [metadata().media[0], { contentUrl: "" }]]) {
        assert.throws(() => postImport.expandImportMedia(Object.assign(metadata(), { media })));
    }
});

test("all supported Inkbunny media types use metadata rather than the original URL extension", () => {
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
    const View = sandbox.module.exports;
    const view = Object.create(View.prototype);
    const input = { value: "" };
    const added = [];
    view._rootNode = { querySelector: () => input };
    view.addUploadables = items => added.push(...items);
    for (const mimeType of [
        "image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp",
        "image/avif", "image/heif", "image/heic", "video/mp4", "video/webm", "video/quicktime",
    ]) {
        view.addImportedPost(Object.assign(metadata(), {
            contentUrl: "https://tx.ib.metapix.net/files/full/without-extension",
            mimeType, importPostUrl: postUrl, postUrl: postUrl + "-p2-",
        }));
        assert.equal(added.at(-1).mimeType, mimeType);
        assert.equal(added.at(-1).type, mimeType.startsWith("image/") ? "image" : "video");
    }
    assert.equal(input.value, postUrl);
    assert.equal(postImport.getImportMimeType("text/html"), null);
});
