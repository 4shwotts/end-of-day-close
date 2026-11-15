// Progressive enhancement for the game: linking rows, the on-screen timer and
// the balanced-day streak. Every page works without this file; it only adds
// conveniences on top of the plain forms.
(function () {
  "use strict";

  var STREAK_KEY = "eod-close.streak";
  var LAST_CASE_KEY = "eod-close.last-case";

  // Storage can be blocked (private windows, strict settings). The streak is
  // a nicety, so failures are ignored rather than breaking the page.
  function read(key, fallback) {
    try {
      var value = window.localStorage.getItem(key);
      return value === null ? fallback : value;
    } catch (error) {
      return fallback;
    }
  }

  function write(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch (error) {
      // Nothing to do: without storage the streak simply is not kept.
    }
  }

  function recordClosedDay() {
    var summary = document.getElementById("close-summary");
    if (!summary) {
      return;
    }
    var caseId = summary.dataset.caseId;
    // Refreshing the close page must not count the same day twice.
    if (read(LAST_CASE_KEY, "") === caseId) {
      return;
    }
    var streak = parseInt(read(STREAK_KEY, "0"), 10) || 0;
    var balanced = summary.dataset.balanced === "true";
    write(STREAK_KEY, String(balanced ? streak + 1 : 0));
    write(LAST_CASE_KEY, caseId);
  }

  function showStreak() {
    var counter = document.getElementById("streak-count");
    if (counter) {
      counter.textContent = read(STREAK_KEY, "0");
    }
  }

  function nextGroupNumber() {
    var highest = 0;
    document.querySelectorAll(".group-input").forEach(function (input) {
      var number = parseInt(input.value, 10);
      if (!isNaN(number) && number > highest) {
        highest = number;
      }
    });
    return highest + 1;
  }

  function setUpLinking() {
    var button = document.getElementById("link-selected");
    var status = document.getElementById("link-status");
    if (!button || !status) {
      return;
    }
    button.hidden = false;
    button.addEventListener("click", function () {
      var picked = Array.prototype.slice.call(document.querySelectorAll(".link-select:checked"));
      if (picked.length < 2) {
        status.textContent = "Select at least two entries to link.";
        return;
      }
      var group = String(nextGroupNumber());
      var ids = picked.map(function (box) {
        var input = document.getElementById("group-" + box.value);
        if (input) {
          input.value = group;
        }
        box.checked = false;
        return box.value;
      });
      status.textContent = "Linked " + ids.join(", ") + " as group " + group + ".";
    });
  }

  function startTimer() {
    var timer = document.getElementById("timer");
    if (!timer) {
      return;
    }
    var started = Date.now();
    timer.hidden = false;
    window.setInterval(function () {
      var seconds = Math.floor((Date.now() - started) / 1000);
      timer.textContent = Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
    }, 1000);
  }

  document.addEventListener("DOMContentLoaded", function () {
    recordClosedDay();
    showStreak();
    setUpLinking();
    startTimer();
  });
})();
