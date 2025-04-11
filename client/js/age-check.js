"use strict";

function setCookie(name, value, days) {
  const d = new Date();
  d.setTime(d.getTime() + (days * 24 * 60 * 60 * 1000));
  document.cookie = name + "=" + value + ";expires=" + d.toUTCString() + ";path=/";
}

function getCookie(name) {
  let match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
  return match ? match[2] : null;
}

window.addEventListener("DOMContentLoaded", function () {
  const overlay = document.getElementById("age-check-overlay");

  if (!getCookie("over18")) {
    overlay.style.display = "flex";

    document.getElementById("age-yes").addEventListener("click", function () {
      setCookie("over18", "yes", 30);
      overlay.classList.add("fade-out");

      setTimeout(() => {
        overlay.style.display = "none";
      }, 500); // match CSS transition time
    });

    document.getElementById("age-no").addEventListener("click", function () {
      window.location.href = "https://www.google.com"; // or any other safe exit
    });
  }
});