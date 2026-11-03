"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const underscore = require("underscore");
const postImport = require("../js/util/post_import.js");

function source(relativePath) {
    return fs.readFileSync(path.join(__dirname, relativePath), "utf8");
}

// Compile the real row with the same engine and context setting as build.js.
const rowTemplate = underscore.template(source("../html/post_upload_row.tpl"), {
    variable: "ctx",
});

function loadRenderingHelpers() {
    const misc = { module: { exports: {} }, require: () => ({}) };
    vm.runInNewContext(source("../js/util/misc.js"), misc);
    const views = {
        module: { exports: {} },
        DOMParser: class {},
        document: { addEventListener() {} },
        require(name) {
            return name === "./misc.js" ? misc.module.exports : {};
        },
    };
    vm.runInNewContext(source("../js/util/views.js") +
        "\nmodule.exports.makeThumbnail = makeThumbnail;", views);
    return views.module.exports;
}

function loadView() {
    const sandbox = {
        module: { exports: {} },
        URL,
        CustomEvent: class {},
        require(name) {
            if (name === "../events.js") return { EventTarget: class { dispatchEvent() {} } };
            if (name === "../api.js") return { isLoggedIn: () => true };
            if (name === "../util/views.js") return { getTemplate: () => rowTemplate };
            if (name === "../util/post_import.js") return postImport;
            return {};
        },
    };
    vm.runInNewContext(source("../js/views/post_upload_view.js"), sandbox);
    return sandbox.module.exports;
}

const helpers = loadRenderingHelpers();
const View = loadView();
const description = "Author: [@test_author](https://x.com/test_author)\n\n**Original description** & notes.";
const postUrl = "https://x.com/test_author/status/123";

for (const media of [
    {
        name: "query-format JPEG photo",
        contentUrl: "https://pbs.twimg.com/media/photo?format=jpg&name=orig",
        mimeType: "image/jpeg",
        flags: [],
    },
    {
        name: "MP4 video with a poster",
        contentUrl: "https://video.twimg.com/ext_tw_video/123/pu/vid/original.mp4",
        mimeType: "video/mp4",
        previewUrl: "https://pbs.twimg.com/ext_tw_video_thumb/123/pu/img/poster.jpg",
        flags: [],
    },
    {
        name: "GIF imported as a looping MP4 with a poster",
        contentUrl: "https://video.twimg.com/tweet_video/original.mp4",
        mimeType: "video/mp4",
        previewUrl: "https://pbs.twimg.com/tweet_video_thumb/poster.jpg",
        flags: ["loop"],
    },
    {
        provider: "inkbunny",
        name: "GIF original with its noncustom thumbnail",
        contentUrl: "https://tx.ib.metapix.net/files/full/123_original.gif",
        mimeType: "image/gif",
        previewUrl: "https://tx.ib.metapix.net/thumbnails/large/123_original.jpg",
        flags: [],
    },
    {
        provider: "inkbunny",
        name: "QuickTime original with its video poster",
        contentUrl: "https://tx.ib.metapix.net/files/full/123_original.mov",
        mimeType: "video/quicktime",
        previewUrl: "https://tx.ib.metapix.net/thumbnails/large/123_video.jpg",
        flags: [],
    },
]) {
    const provider = media.provider || "x";
    test("real upload row renders " + provider + " " + media.name, () => {
        const metadata = {
            provider, postUrl, description,
            author: { username: "test_author", url: "https://x.com/test_author" },
            sources: [postUrl], tags: {}, safety: "unsafe", warnings: [],
            media: [Object.assign({ postUrl }, media)],
        };
        const view = Object.create(View.prototype);
        view._rootNode = { querySelector: () => ({ value: "" }) };
        let uploadable;
        view.addUploadables = (items) => { [uploadable] = items; };
        view.addImportedPost(postImport.expandImportMedia(metadata)[0]);

        const html = rowTemplate({
            uploadable,
            enableSafety: false,
            canUploadAnonymously: false,
            makeThumbnail: helpers.makeThumbnail,
        });
        const thumbnail = html.split("<div class='uploadable'>")[0];
        const previewUrl = media.previewUrl || media.contentUrl;

        assert.ok(thumbnail.includes("href='" + underscore.escape(media.contentUrl) + "'"),
            "preview link must retain the original media URL");
        assert.ok(thumbnail.includes('src="' + previewUrl.replace(/&/g, "&amp;") + '"'),
            "preview image must use the photo or video poster");
        assert.match(thumbnail, /background-image: url/);
        assert.doesNotMatch(thumbnail, /thumbnail empty|<video\b/);
        assert.equal(uploadable.url, media.contentUrl);
        assert.equal(uploadable.mimeType, media.mimeType);
        assert.equal(uploadable.description, description);
        assert.ok(html.includes(underscore.escape(description)),
            "editable description must remain intact and HTML-escaped");
        assert.deepEqual(Array.from(uploadable.flags), media.flags);
    });
}

for (const type of ["image", "video"]) {
    test("upload row keeps quote-bearing " + type + " URLs and filenames as data", () => {
        const url = "https://media.example/original' onmouseover='alert(1).mp4?x=\"a\"&y=<tag>";
        const name = 'draft"><img src=x onerror=alert(1)>';
        const uploadable = {
            type, url, previewUrl: url, importPreviewUrl: null,
            mimeType: type === "video" ? "video/mp4" : "image/jpeg",
            name, key: url, importInfo: null,
            source: "", manualTags: "", description: "", lookalikes: [],
        };
        const html = rowTemplate({
            uploadable,
            enableSafety: false,
            canUploadAnonymously: false,
            makeThumbnail: helpers.makeThumbnail,
        });
        const anchor = html.match(/<a href='([^']*)'>/);
        assert.ok(anchor, "thumbnail link must stay within its quoted attribute");
        assert.equal(anchor[1], underscore.escape(url));
        assert.ok(html.includes("<span class='filename'>" + underscore.escape(name) + "</span>"));
        assert.doesNotMatch(html, /<img src=x onerror=|href='[^']*' onmouseover=/);
        if (type === "video") {
            assert.ok(html.includes("src='" + underscore.escape(url) + "'"));
        }
        assert.equal(uploadable.url, url, "escaping must not change the upload source");
        assert.equal(uploadable.name, name);
    });
}

test("thumbnail background encodes CSS delimiters while its image keeps the original URL", () => {
    const url = "https://media.example/original');position:fixed;content:\"x\"\\\n\r\f.jpg?x=1&y=2";
    const html = helpers.makeThumbnail(url);
    const expectedBackground = "https://media.example/original%27);position:fixed;content:%22x%22%5C%0A%0D%0C.jpg?x=1&amp;y=2";
    assert.ok(html.includes('style="background-image: url(&apos;' + expectedBackground + '&apos;)"'));
    assert.ok(html.includes('src="https://media.example/original&apos;);position:fixed;content:&quot;x&quot;\\\n\r\f.jpg?x=1&amp;y=2"'),
        "the hidden image must retain the exact original URL after HTML decoding");
    assert.doesNotMatch(html.match(/style="([^"]*)"/)[1], /\n|\r|\f|\\/);
});
