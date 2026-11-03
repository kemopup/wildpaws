"use strict";

const labels = {
    safe: "Arf",
    sketchy: "Murr?",
    unsafe: "Huff!",
};

function formatSafetyLabel(value) {
    const key = String(value || "").toLowerCase();
    const aliases = {
        arf: "safe", "murr?": "sketchy", murr: "sketchy",
        "huff!": "unsafe", huff: "unsafe",
    };
    return labels[aliases[key] || key] || String(value || "");
}

module.exports = { formatSafetyLabel };
