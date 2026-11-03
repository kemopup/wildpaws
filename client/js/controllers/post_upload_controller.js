"use strict";

const api = require("../api.js");
const router = require("../router.js");
const uri = require("../util/uri.js");
const misc = require("../util/misc.js");
const progress = require("../util/progress.js");
const topNavigation = require("../models/top_navigation.js");
const Post = require("../models/post.js");
const Tag = require("../models/tag.js");
const PostUploadView = require("../views/post_upload_view.js");
const EmptyView = require("../views/empty_view.js");
const { expandImportMedia, selectedImportCategories } = require("../util/post_import.js");

const genericErrorMessage =
    "One or more posts needs your attention; " +
    'click "resume upload" when you\'re ready.';

class PostUploadController {
    constructor() {
        this._lastCancellablePromise = null;
        this._importRequest = null;
        this._previewRequests = new Set();
        this._inkbunnyPreviewQueue = Promise.resolve();
        this._previewRequestsAborted = false;

        if (!api.hasPrivilege("posts:create")) {
            this._view = new EmptyView();
            this._view.showError("You don't have privileges to upload posts.");
            return;
        }

        topNavigation.activate("upload");
        topNavigation.setTitle("Upload");
        this._view = new PostUploadView({
            canUploadAnonymously: api.hasPrivilege("posts:create:anonymous"),
            canViewPosts: api.hasPrivilege("posts:view"),
            enableSafety: api.safetyEnabled(),
        });
        this._view.addEventListener("change", (e) => this._evtChange(e));
        this._view.addEventListener("submit", (e) => this._evtSubmit(e));
        this._view.addEventListener("cancel", (e) => this._evtCancel(e));
        this._view.addEventListener("import", (e) => this._evtImport(e));
        this._view.addEventListener("leave", () => {
            this._abortImportRequests();
        });
    }

    _evtImport(e) {
        if (this._importRequest) {
            return;
        }
        this._view.setImporting(true);
        this._view.clearImportMessages();
        const request = api.post(uri.formatApiLink("import-preview"), {
            url: e.detail.url,
        });
        this._importRequest = request;
        const finish = () => {
            this._importRequest = null;
            if (this._view.active) {
                this._view.setImporting(false);
            }
        };
        request.then((metadata) => {
            if (this._view.active) {
                for (const item of expandImportMedia(metadata)) {
                    const uploadable = this._view.addImportedPost(item);
                    this._loadImportThumbnail(item, uploadable);
                }
            }
        }, (error) => {
            if (this._view.active) {
                this._view.showImportError(error.message);
            }
        }).then(finish, (error) => {
            if (this._view.active) {
                this._view.showImportError(error.message);
            }
            finish();
        });
    }

    _abortImportRequests() {
        this._previewRequestsAborted = true;
        if (this._importRequest) {
            this._importRequest.abort();
        }
        for (const request of this._previewRequests) {
            request.abort();
        }
        this._previewRequests.clear();
    }

    _loadImportThumbnail(metadata, uploadable) {
        if (!["gelbooru", "inkbunny", "furaffinity", "bluesky"].includes(metadata.provider) ||
            this._previewRequestsAborted || !this._view.hasUploadable(uploadable)) {
            return;
        }
        if (["inkbunny", "furaffinity", "bluesky"].includes(metadata.provider)) {
            // Provider sessions fetch display previews one at a time
            // while keeping the upload form available immediately.
            const previous = this._inkbunnyPreviewQueue || Promise.resolve();
            this._inkbunnyPreviewQueue = previous.catch(() => {}).then(() => {
                if (!this._previewRequestsAborted && this._view.active &&
                    this._view.hasUploadable(uploadable)) {
                    return this._requestImportThumbnail(metadata, uploadable);
                }
            });
            return this._inkbunnyPreviewQueue;
        }
        return this._requestImportThumbnail(metadata, uploadable);
    }

    _requestImportThumbnail(metadata, uploadable) {
        const request = api.post(uri.formatApiLink("import-thumbnail"), {
            url: metadata.postUrl,
        });
        this._previewRequests.add(request);
        return request.then((response) => {
            if (!this._previewRequestsAborted && this._view.hasUploadable(uploadable)) {
                if (!response || typeof response.previewUrl !== "string" ||
                    !/^data:image\/jpeg;base64,[A-Za-z0-9+/]+=*$/.test(response.previewUrl)) {
                    throw new Error("Invalid preview response.");
                }
                this._view.setImportedPreview(uploadable, response.previewUrl);
            }
        }).catch(() => {
            if (!this._previewRequestsAborted && this._view.hasUploadable(uploadable)) {
                this._view.showInfo(
                    "Preview unavailable. You can still upload this post.",
                    uploadable
                );
            }
        }).then(() => {
            this._previewRequests.delete(request);
        });
    }

    _evtChange(e) {
        if (e.detail.uploadables.length) {
            misc.enableExitConfirmation();
        } else {
            misc.disableExitConfirmation();
            this._view.clearMessages();
        }
    }

    _evtCancel(e) {
        if (this._lastCancellablePromise) {
            this._lastCancellablePromise.abort();
        }
    }

    _evtSubmit(e) {
        if (this._importRequest) {
            return;
        }
        this._view.disableForm();
        this._view.clearMessages();
        let anyFailures = false;

        e.detail.uploadables
            .reduce(
                (promise, uploadable) =>
                    promise.then(() =>
                        this._uploadSinglePost(
                            uploadable,
                            e.detail.skipDuplicates,
                            e.detail.alwaysUploadSimilar
                        ).catch((error) => {
                            anyFailures = true;
                            if (error.uploadable) {
                                if (error.similarPosts) {
                                    error.uploadable.lookalikes =
                                        error.similarPosts;
                                    this._view.updateUploadable(
                                        error.uploadable
                                    );
                                    this._view.showInfo(
                                        error.message,
                                        error.uploadable
                                    );
                                } else {
                                    this._view.showError(
                                        error.message,
                                        error.uploadable
                                    );
                                }
                            } else {
                                this._view.showError(
                                    error.message,
                                    uploadable
                                );
                            }
                            if (e.detail.pauseRemainOnError) {
                                return Promise.reject();
                            }
                        })
                    ),
                Promise.resolve()
            )
            .then(() => {
                if (anyFailures) {
                    return Promise.reject();
                }
            })
            .then(
                () => {
                    this._view.clearMessages();
                    misc.disableExitConfirmation();
                    const ctx = router.show(uri.formatClientLink("posts"));
                    ctx.controller.showSuccess("Posts uploaded.");
                },
                (error) => {
                    this._view.showError(genericErrorMessage);
                    this._view.enableForm();
                }
            );
    }

    _uploadSinglePost(uploadable, skipDuplicates, alwaysUploadSimilar) {
        progress.start();
        let reverseSearchPromise = Promise.resolve();
        if (!uploadable.lookalikesConfirmed) {
            reverseSearchPromise = Post.reverseSearch(
                uploadable.url || uploadable.file
            );
        }
        this._lastCancellablePromise = reverseSearchPromise;

        return reverseSearchPromise
            .then((searchResult) => {
                if (searchResult) {
                    // notify about exact duplicate
                    if (searchResult.exactPost) {
                        if (skipDuplicates) {
                            this._view.removeUploadable(uploadable);
                            return Promise.resolve();
                        } else {
                            let error = new Error(
                                "Post already uploaded " +
                                    `(@${searchResult.exactPost.id})`
                            );
                            error.uploadable = uploadable;
                            return Promise.reject(error);
                        }
                    }

                    // notify about similar posts
                    if (
                        searchResult.similarPosts.length &&
                        !alwaysUploadSimilar
                    ) {
                        let error = new Error(
                            `Found ${searchResult.similarPosts.length} similar ` +
                                "posts.\nYou can resume or discard this upload."
                        );
                        error.uploadable = uploadable;
                        error.similarPosts = searchResult.similarPosts;
                        return Promise.reject(error);
                    }
                }

                // no duplicates, proceed with saving
                let post = this._uploadableToPost(uploadable);
                let savePromise = post.save(uploadable.anonymous).then(() => {
                    this._view.removeUploadable(uploadable);
                    return Promise.resolve();
                });
                this._lastCancellablePromise = savePromise;
                return savePromise;
            })
            .then(
                (result) => {
                    progress.done();
                    return Promise.resolve(result);
                },
                (error) => {
                    error.uploadable = uploadable;
                    progress.done();
                    return Promise.reject(error);
                }
            );
    }

    _uploadableToPost(uploadable) {
        let post = new Post();
        post.safety = uploadable.safety;
        post.description = uploadable.description;
        post.flags = uploadable.flags;
        for (let tagName of uploadable.tags) {
            const tag = new Tag();
            tag.names = [tagName];
            post.tags.add(tag);
        }
        post.relations = uploadable.relations;
        post.newContent = uploadable.url || uploadable.file;
        post.source = uploadable.source || uploadable.url || "";
        if (uploadable.importTagCategories) {
            post.importTagCategories = selectedImportCategories(
                uploadable.importTagCategories, uploadable.tags
            );
        }
        return post;
    }
}

module.exports = (router) => {
    router.enter(["upload"], (ctx, next) => {
        ctx.controller = new PostUploadController();
    });
};
