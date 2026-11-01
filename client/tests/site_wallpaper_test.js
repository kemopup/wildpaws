"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const uri = require("../js/util/uri.js");

function environment() {
    const properties = new Map();
    const images = [];
    const listeners = {};
    const api = {
        userName: "tester",
        user: { name: "tester", version: 3, wallpaper: null },
        addEventListener: (event, listener) => { listeners[event] = listener; },
    };
    const sandbox = {
        module: { exports: {} }, URL,
        require: (name) => name === "./api.js" ? api : uri,
        document: {
            baseURI: "https://wildpaws.test/gallery/",
            documentElement: { style: {
                removeProperty: (key) => properties.delete(key),
                setProperty: (key, value) => properties.set(key, value),
            } },
        },
        Image: class { constructor() { images.push(this); } },
    };
    vm.runInNewContext(fs.readFileSync(
        path.join(__dirname, "../js/site_wallpaper.js"), "utf8"
    ), sandbox);
    return { wallpaper: sandbox.module.exports, api, images, properties, listeners };
}

test("logout prevents a pending image from exposing the previous user's wallpaper", () => {
    const env = environment();
    env.api.user.wallpaper = { contentUrl: "data/posts/1.jpg" };
    env.wallpaper.initialize();
    assert.equal(env.images[0].src, "https://wildpaws.test/gallery/data/posts/1.jpg");
    env.listeners.logout();
    env.images[0].onload();
    assert.equal(env.properties.size, 0);
});

test("only the latest chosen wallpaper is applied", () => {
    const env = environment();
    env.wallpaper.apply({ wallpaper: { contentUrl: "data/posts/1.jpg" } });
    env.wallpaper.apply({ wallpaper: { contentUrl: "data/posts/2.jpg" } });
    env.images[0].onload();
    assert.equal(env.properties.size, 0);
    env.images[1].onload();
    assert.match(env.properties.get("--site-wallpaper-image"), /posts\/2.jpg/);
    assert.equal(env.properties.get("--site-wallpaper-size"), "cover");
});

test("invalid wallpaper URLs fall back to the default", () => {
    const env = environment();
    env.wallpaper.apply({ wallpaper: { contentUrl: "javascript:alert(1)" } });
    assert.equal(env.images.length, 0);
    assert.equal(env.properties.size, 0);
});

test("selection and reset update only the account wallpaper with the current version", async () => {
    const env = environment();
    const writes = [];
    env.api.get = async () => ({ version: 7 });
    env.api.put = async (endpoint, body) => {
        writes.push({ endpoint, body: JSON.parse(JSON.stringify(body)) });
        return {
            name: "tester", version: 8,
            wallpaper: body.wallpaperPostId === null ? null : {
                postId: body.wallpaperPostId, contentUrl: "data/posts/3.jpg",
            },
        };
    };
    await env.wallpaper.setPost(3);
    assert.deepEqual(writes[0], {
        endpoint: "/user/tester", body: { version: 7, wallpaperPostId: 3 },
    });
    assert.equal(env.api.user.wallpaper.postId, 3);
    env.images[0].onload();
    assert.equal(env.properties.size, 3);
    await env.wallpaper.setPost(null);
    assert.deepEqual(writes[1].body, { version: 7, wallpaperPostId: null });
    assert.equal(env.api.user.wallpaper, null);
    assert.equal(env.properties.size, 0);
});
