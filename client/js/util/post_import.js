"use strict";

const importMimeTypes = new Set([
    "image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp",
    "image/avif", "image/heif", "image/heic", "video/mp4", "video/webm",
    "video/quicktime",
]);

function getImportMimeType(value) {
    return importMimeTypes.has(value) ? value : null;
}

function hasUncategorizedTags(provider) {
    return ["x", "inkbunny", "furaffinity", "bluesky"].includes(provider);
}

function uniqueStrings(values, ignoreCase = true) {
    const seen = new Set();
    return values.filter((value) => {
        if (typeof value !== "string" || !value.trim()) {
            return false;
        }
        const key = ignoreCase ? value.toLowerCase() : value;
        if (seen.has(key)) {
            return false;
        }
        seen.add(key);
        return true;
    });
}

function createImportDraft(metadata) {
    const categories = hasUncategorizedTags(metadata.provider) ? {} : metadata.tags || {};
    // Deliberately import only these two categories, even if a provider
    // response gains additional tag groups in the future.
    const artist = uniqueStrings(categories.artist || []);
    const character = uniqueStrings(categories.character || []);
    const tags = uniqueStrings(artist.concat(character));
    const draft = {
        url: metadata.contentUrl,
        source: uniqueStrings([metadata.postUrl].concat(metadata.sources || []), false).join("\n"),
        tags: tags,
        manualTags: tags.join(" "),
        importTagCategories: { artist: artist, character: character },
        safety: metadata.safety,
        importMimeType: getImportMimeType(metadata.mimeType),
        importPreviewUrl: importPreviewUrl(metadata.provider, metadata.previewUrl),
        description: typeof metadata.description === "string" ? metadata.description : "",
        importInfo: {
            provider: metadata.provider,
            postUrl: metadata.postUrl,
            artist: artist,
            character: character,
            warnings: metadata.warnings || [],
            author: metadata.author || null,
        },
    };
    if (hasUncategorizedTags(metadata.provider)) {
        draft.flags = Array.isArray(metadata.flags) && metadata.flags.includes("loop")
            ? ["loop"] : [];
    }
    return draft;
}

function expandImportMedia(metadata) {
    if (!hasUncategorizedTags(metadata.provider) || !Array.isArray(metadata.media)) {
        return [metadata];
    }
    if (!metadata.media.length || metadata.media.some((item) =>
        !item || typeof item.contentUrl !== "string" || !item.contentUrl)) {
        throw new Error("No supported images or videos were supplied for this post.");
    }
    return metadata.media.map((item) => Object.assign({}, metadata, {
        contentUrl: item.contentUrl,
        mimeType: item.mimeType,
        previewUrl: item.previewUrl || null,
        flags: Array.isArray(item.flags) && item.flags.includes("loop") ? ["loop"] : [],
        postUrl: item.postUrl || metadata.postUrl,
        importPostUrl: metadata.postUrl,
        media: undefined,
    }));
}

function importPreviewUrl(provider, value) {
    if (typeof value !== "string" || /[\x00-\x20\x7f'"\\]/.test(value)) {
        return null;
    }
    try {
        const url = new URL(value);
        if (url.protocol !== "https:" || url.username || url.password ||
            (url.port && url.port !== "443")) {
            return null;
        }
        if (provider === "x") {
            return url.hostname === "pbs.twimg.com" ? value : null;
        }
        if (provider === "inkbunny" && !url.search && !url.hash) {
            return ["inkbunny.net", "ib.metapix.net"].some(domain =>
                url.hostname === domain || url.hostname.endsWith("." + domain))
                ? value : null;
        }
        if (provider === "furaffinity" && !url.search && !url.hash) {
            return url.hostname === "t.furaffinity.net" ? value : null;
        }
        if (provider === "bluesky" && !url.search && !url.hash) {
            return ["cdn.bsky.app", "video.bsky.app"].includes(url.hostname)
                ? value : null;
        }
        return null;
    } catch (_error) {
        return null;
    }
}

function selectedImportCategories(categories, selectedTags) {
    const selected = new Set(selectedTags.map((name) => name.toLowerCase()));
    const result = {};
    for (const category of ["artist", "character"]) {
        result[category] = (categories[category] || [])
            .filter((name) => selected.has(name.toLowerCase()));
    }
    return result;
}

module.exports = { createImportDraft, expandImportMedia, getImportMimeType, selectedImportCategories };
