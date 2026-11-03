"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const { createImportDraft, selectedImportCategories } = require("../js/util/post_import.js");

test("import draft keeps original file, sources, rating, and only artist/character tags", () => {
    const draft = createImportDraft({
        provider: "e621", postUrl: "https://e621.net/posts/123",
        contentUrl: "https://static1.e621.net/data/original.png",
        sampleUrl: "https://static1.e621.net/data/sample.jpg",
        sources: ["https://artist.example/art", "https://artist.example/art", "https://e621.net/posts/123"],
        tags: {
            artist: ["artist_name", "ARTIST_NAME"], character: ["character_name"],
            general: ["unwanted_general"], species: ["unwanted_species"],
            meta: ["unwanted_meta"], copyright: ["unwanted_copyright"],
        },
        safety: "unsafe", description: "## Imported description\n\n**Artist's note**", warnings: [],
    });
    assert.equal(draft.url, "https://static1.e621.net/data/original.png");
    assert.equal(draft.source, "https://e621.net/posts/123\nhttps://artist.example/art");
    assert.equal(draft.manualTags, "artist_name character_name");
    assert.deepEqual(draft.importTagCategories, {artist: ["artist_name"], character: ["character_name"]});
    assert.equal(draft.safety, "unsafe");
    assert.equal(draft.description, "## Imported description\n\n**Artist's note**");
});

test("sites without artists leave artist category empty and retain their warnings", () => {
    const draft = createImportDraft({
        provider: "e6ai", postUrl: "https://e6ai.net/posts/123",
        contentUrl: "https://e6ai.net/data/original.png", sources: [],
        tags: { character: ["character_name"], general: ["not_an_artist"] },
        safety: "safe", warnings: ["No artist tags supplied."],
    });
    assert.deepEqual(draft.tags, ["character_name"]);
    assert.deepEqual(draft.importTagCategories.artist, []);
    assert.deepEqual(draft.importInfo.warnings, ["No artist tags supplied."]);
    assert.equal(draft.description, "");
});

test("source URLs with different path casing stay distinct", () => {
    const draft = createImportDraft({
        provider: "e621", postUrl: "https://e621.net/posts/123",
        contentUrl: "https://static1.e621.net/data/original.png",
        sources: ["https://artist.example/Art", "https://artist.example/art"],
        tags: {}, safety: "safe",
    });
    assert.equal(draft.source.split("\n").length, 3);
});

test("removing an imported tag excludes its category metadata; manual tags are not categorized", () => {
    assert.deepEqual(selectedImportCategories({
        artist: ["artist_name"], character: ["character_name", "removed_character"],
        general: ["not_allowed"],
    }, ["ARTIST_NAME", "character_name", "manual_tag"]), {
        artist: ["artist_name"], character: ["character_name"],
    });
});

function makePostSaveFixture(id) {
    const calls = [];
    const api = {};
    for (const method of ["post", "put"]) {
        api[method] = async (url, body, files) => {
            calls.push({method, url, body: JSON.parse(JSON.stringify(body)), files});
            return {};
        };
    }
    const sandbox = {
        module: {exports: {}}, CustomEvent: class {},
        require(name) {
            if (name === "../api.js") return api;
            if (name === "../util/uri.js") return {formatApiLink: (...parts) => "/" + parts.join("/")};
            if (name === "../events.js") return {EventTarget: class {}};
            if (name === "../util/misc.js") return {arraysDiffer: (a, b) => JSON.stringify(a) !== JSON.stringify(b)};
            return class {};
        },
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../js/models/post.js"), "utf8"), sandbox);
    const post = Object.create(sandbox.module.exports.prototype);
    Object.assign(post, {
        _id: id, _version: 1, _safety: "unsafe", _flags: [],
        _tags: [{names: ["artist_name"]}], _relations: [], _notes: [], _pools: [],
        _source: "https://e621.net/posts/123\nhttps://artist.example/art",
        _description: "## Imported description\n\n**Artist's note**", _newContent: "https://static1.e621.net/data/original.png",
        importTagCategories: {artist: ["artist_name"], character: []},
        _orig: {_safety: "safe", _flags: [], _tags: [], _relations: [], _notes: [], _pools: [], _source: "", _description: ""},
        _updateFromResponse() {}, dispatchEvent() {},
    });
    return {post, calls};
}

test("new upload sends sources and imported categories alongside original content", async () => {
    const {post, calls} = makePostSaveFixture(undefined);
    await post.save(false);
    assert.equal(calls[0].method, "post");
    assert.equal(calls[0].body.source, "https://e621.net/posts/123\nhttps://artist.example/art");
    assert.equal(calls[0].body.description, "## Imported description\n\n**Artist's note**");
    assert.deepEqual(calls[0].body.tagCategories, {artist: ["artist_name"], character: []});
    assert.equal(calls[0].files.content, "https://static1.e621.net/data/original.png");
});

test("editing an existing post never sends importer category metadata", async () => {
    const {post, calls} = makePostSaveFixture(7);
    await post.save(false);
    assert.equal(calls[0].method, "put");
    assert.equal(Object.hasOwn(calls[0].body, "tagCategories"), false);
});
