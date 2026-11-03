"use strict";

const settings = require("../models/settings.js");
const topNavigation = require("../models/top_navigation.js");
const SettingsView = require("../views/settings_view.js");
const api = require("../api.js");
const siteWallpaper = require("../site_wallpaper.js");

class SettingsController {
    constructor() {
        topNavigation.activate("settings");
        topNavigation.setTitle("Browsing settings");
        this._view = new SettingsView({
            settings: settings.get(),
            user: api.user,
            canSetWallpaper: api.isLoggedIn() &&
                api.hasPrivilege("users:edit:self:wallpaper"),
        });
        this._view.addEventListener("submit", (e) => this._evtSubmit(e));
        this._view.addEventListener("resetWallpaper", () => {
            this._view.setWallpaperPending(true);
            siteWallpaper.setPost(null).then(
                () => this._view.wallpaperReset(),
                (error) => this._view.showWallpaperError(error.message)
            );
        });
    }

    _evtSubmit(e) {
        this._view.clearMessages();
        settings.save(e.detail);
        this._view.showSuccess("Settings saved.");
    }
}

module.exports = (router) => {
    router.enter(["settings"], (ctx, next) => {
        ctx.controller = new SettingsController();
    });
};
