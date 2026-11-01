"use strict";

const labels = {
    safe: "Arf",
    sketchy: "Merr?",
    unsafe: "Huff!",
};

function formatSafetyLabel(value) {
    const key = String(value || "").toLowerCase();
    const aliases = {
        arf: "safe", "merr?": "sketchy", merr: "sketchy",
        "huff!": "unsafe", huff: "unsafe",
    };
    return labels[aliases[key] || key] || String(value || "");
}

module.exports = { formatSafetyLabel };
