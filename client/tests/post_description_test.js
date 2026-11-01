"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function makePost(put) {
    const sandbox = {
        module: { exports: {} },
        require: (name) => {
            if (name === "../api.js") {
                return { put };
            }
            if (name === "../util/uri.js") {
                return { formatApiLink: (...parts) => "/api/" + parts.join("/") };
            }
            if (name === "../events.js") {
                return { EventTarget: class {} };
            }
            return class {};
        },
    };
    vm.runInNewContext(fs.readFileSync(
        path.join(__dirname, "../js/models/post.js"), "utf8"
    ), sandbox);
    const post = Object.create(sandbox.module.exports.prototype);
    Object.assign(post, {
        _id: 3, _version: 7, _description: "Original",
        _tags: ["pending-tag"], _notes: ["pending-note"],
        _source: "pending source", _newContent: "pending content",
        _pools: ["pending pool"],
        _orig: {
            _version: 7, _description: "Original",
            _tags: ["saved-tag"], _notes: [], _source: "saved source",
            _pools: [],
        },
    });
    return post;
}

test("description save excludes other edits and preserves pending changes", async () => {
    const calls = [];
    const post = makePost(async (...args) => {
        calls.push(args);
        return { version: 8, description: "Updated **Markdown**", tags: [] };
    });
    const tags = post._tags;
    const notes = post._notes;
    const pools = post._pools;
    await post.saveDescription("Updated **Markdown**");
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], "/api/post/3");
    assert.deepEqual(JSON.parse(JSON.stringify(calls[0][1])), {
        version: 7, description: "Updated **Markdown**",
    });
    assert.equal(calls[0].length, 2);
    assert.equal(post._tags, tags);
    assert.equal(post._notes, notes);
    assert.equal(post._pools, pools);
    assert.equal(post._source, "pending source");
    assert.equal(post._newContent, "pending content");
    assert.deepEqual(post._orig._tags, ["saved-tag"]);
    assert.equal(post.description, "Updated **Markdown**");
    assert.equal(post._orig._description, post.description);
    assert.equal(post._version, 8);
    assert.equal(post._orig._version, 8);
});

test("failed description save leaves saved values and version unchanged", async () => {
    const post = makePost(async () => { throw new Error("Version conflict"); });
    await assert.rejects(post.saveDescription("Unsaved"), /Version conflict/);
    assert.equal(post.description, "Original");
    assert.equal(post._orig._description, "Original");
    assert.equal(post._version, 7);
    assert.deepEqual(post._tags, ["pending-tag"]);
});
