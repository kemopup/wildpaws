"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function fixture() {
    class List {
        constructor() { this.items = []; }
        sync(items) { this.items = items.map(x => ({ ...x })); }
        clear() { this.items = []; }
        add(item) { this.items.push(item); }
        map(fn) { return this.items.map(fn); }
        get length() { return this.items.length; }
        [Symbol.iterator]() { return this.items[Symbol.iterator](); }
    }
    const response = {
        id: 1, version: 3, names: ["ordered"], category: "default",
        description: "", posts: [7, 4, 6, 5].map(id => ({ id })),
        postsMicro: [7, 4, 6].map(id => ({ id })),
    };
    const calls = [];
    const api = { put: async (url, body) => {
        calls.push(JSON.parse(JSON.stringify(body)));
        return { ...response, posts: (body.posts || response.posts.map(x => x.id)).map(id => ({ id })) };
    } };
    const sandbox = {
        module: { exports: {} }, CustomEvent: class {},
        require: name => {
            if (name === "../api.js") return api;
            if (name === "../util/uri.js") return { formatApiLink: (...p) => p.join("/") };
            if (name === "../events.js") return { EventTarget: class { dispatchEvent() {} } };
            if (name === "./post_list.js") return List;
            return { arraysDiffer: (a, b, ordered) => {
                if (ordered) return a.length !== b.length || a.some((x, i) => x !== b[i]);
                return a.some(x => !b.includes(x)) || b.some(x => !a.includes(x));
            } };
        },
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../js/models/pool.js"), "utf8"), sandbox);
    return { Pool: sandbox.module.exports, response, calls };
}

test("full pool membership wins over the three preview posts", () => {
    const { Pool, response } = fixture();
    const pool = Pool.fromResponse(response);
    assert.deepEqual(pool.posts.map(x => x.id), [7, 4, 6, 5]);
    assert.equal(typeof pool.posts.clear, "function");
    const preview = Pool.fromResponse({ ...response, posts: undefined });
    assert.deepEqual(preview.posts.map(x => x.id), [7, 4, 6]);
});

test("saving unchanged pool IDs sends no membership change", async () => {
    const { Pool, response, calls } = fixture();
    await Pool.fromResponse(response).save();
    assert.deepEqual(calls, [{ version: 3 }]);
});

test("reordering the same posts saves every ID in the requested order", async () => {
    const { Pool, response, calls } = fixture();
    const pool = Pool.fromResponse(response);
    pool.posts.clear();
    [5, 6, 4, 7].forEach(id => pool.posts.add({ id }));
    await pool.save();
    assert.deepEqual(calls[0].posts, [5, 6, 4, 7]);
    assert.deepEqual(pool.posts.map(x => x.id), [5, 6, 4, 7]);
});
