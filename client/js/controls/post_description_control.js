"use strict";

const events = require("../events.js");
const views = require("../util/views.js");
const markdown = require("../util/markdown.js");

class PostDescriptionControl extends events.EventTarget {
    constructor(hostNode, post) {
        super();
        this._hostNode = hostNode;
        this._post = post;
        this._form = hostNode.querySelector("#post-description-form");
        if (!this._form) {
            return;
        }
        this._input = this._form.querySelector("textarea");
        this._edit = hostNode.querySelector(".edit-description");
        this._readonly = hostNode.querySelector(".description-readonly");
        this._preview = this._form.querySelector("#post-description-preview");
        this._toggle = this._form.querySelector(".description-preview-toggle");

        this._edit.addEventListener("click", () => {
            views.clearMessages(this._hostNode);
            this._input.value = this._post.description;
            this._form.hidden = false;
            this._readonly.hidden = true;
            this._edit.hidden = true;
            this._edit.setAttribute("aria-expanded", "true");
            this._input.focus();
        });
        this._form.querySelector(".description-cancel").addEventListener(
            "click", () => this.exitEditMode()
        );
        this._toggle.addEventListener("click", () => {
            this._preview.hidden = !this._preview.hidden;
            this._toggle.setAttribute("aria-expanded", String(!this._preview.hidden));
            this._toggle.textContent = this._preview.hidden
                ? "Show preview" : "Hide preview";
            this._updatePreview();
        });
        this._input.addEventListener("input", () => {
            this._updatePreview();
            this._emitChange(this._input.value !== this._post.description);
        });
        this._form.addEventListener("submit", (e) => {
            e.preventDefault();
            this.dispatchEvent(new CustomEvent("submit", {
                detail: { description: this._input.value },
            }));
        });
    }

    _updatePreview() {
        if (!this._preview.hidden) {
            this._preview.innerHTML = markdown.formatMarkdown(this._input.value, true);
        }
    }

    _emitChange(dirty) {
        this.dispatchEvent(new CustomEvent("change", { detail: { dirty } }));
    }

    exitEditMode() {
        this._form.hidden = true;
        this._readonly.hidden = false;
        this._edit.hidden = false;
        this._edit.setAttribute("aria-expanded", "false");
        this._input.value = this._post.description;
        this._preview.hidden = true;
        this._toggle.setAttribute("aria-expanded", "false");
        this._toggle.textContent = "Show preview";
        this._readonly.innerHTML = this._post.description
            ? markdown.formatMarkdown(this._post.description, true)
            : "<p class='empty-description'>No description yet.</p>";
        views.clearMessages(this._hostNode);
        this._emitChange(false);
        this._edit.focus();
    }

    disableForm() {
        views.clearMessages(this._hostNode);
        views.disableForm(this._form);
    }

    enableForm() {
        views.enableForm(this._form);
    }

    showSuccess(message) {
        views.showSuccess(this._hostNode, message);
    }

    showError(message) {
        views.showError(this._hostNode, message);
    }
}

module.exports = PostDescriptionControl;
