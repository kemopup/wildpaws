"use strict";

const api = require("./api.js");
const uri = require("./util/uri.js");

let requestId = 0;

function apply(user) {
    const currentRequest = ++requestId;
    const root = document.documentElement;
    for (const property of ["image", "size", "repeat"]) {
        root.style.removeProperty("--site-wallpaper-" + property);
    }
    if (!user || !user.wallpaper) {
        return;
    }
    let url;
    try {
        url = new URL(user.wallpaper.contentUrl, document.baseURI);
        if (!["http:", "https:"].includes(url.protocol)) {
            return;
        }
    } catch (error) {
        return;
    }
    const image = new Image();
    image.onload = () => {
        if (currentRequest !== requestId) {
            return;
        }
        root.style.setProperty("--site-wallpaper-image", "url(" + JSON.stringify(url.href) + ")");
        root.style.setProperty("--site-wallpaper-size", "cover");
        root.style.setProperty("--site-wallpaper-repeat", "no-repeat");
    };
    image.src = url.href;
}

function initialize() {
    api.addEventListener("login", () => apply(api.user));
    api.addEventListener("logout", () => apply(null));
    apply(api.user);
}

function setPost(postId) {
    const userName = api.userName;
    if (!api.user || !userName) {
        return Promise.reject(new Error("Log in to choose a site wallpaper."));
    }
    const endpoint = uri.formatApiLink("user", userName);
    return api.get(endpoint).then((user) => api.put(endpoint, {
        version: user.version,
        wallpaperPostId: postId,
    })).then((user) => {
        if (api.userName === userName) {
            api.user = user;
            apply(user);
        }
        return user;
    });
}

module.exports = { initialize, apply, setPost };
