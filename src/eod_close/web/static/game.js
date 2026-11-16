// Progressive enhancement for the game: linking rows, paging the tables and
// the on-screen timer. Every page works without this file; it only adds
// conveniences on top of the plain forms.
(function () {
  "use strict";

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
        status.textContent = "Select at least two lines to link.";
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
      status.textContent = "Linked " + ids.join(", ") + " as pair " + group + ".";
    });
  }

  // The clock is a role="timer", which screen readers leave alone between
  // ticks; it only changes colour, gently and once each, at five and ten
  // minutes (data-stage, styled in app.css), and never flashes.
  var STAGES = [
    [600, "later"],
    [300, "late"],
  ];

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
      for (var i = 0; i < STAGES.length; i += 1) {
        if (seconds >= STAGES[i][0]) {
          timer.dataset.stage = STAGES[i][1];
          break;
        }
      }
    }, 1000);
  }

  // Long tables (Senior Analyst and Head of Ops) are shown a page of rows at
  // a time, with Previous and Next. Rows on other pages are only hidden, so
  // their pair numbers and choices stay in the form and are sent when the
  // day is closed. Without this script every row simply shows.
  var PAGE_ROWS = 20;

  function pageTable(table) {
    var rows = Array.prototype.slice.call(table.tBodies[0].rows);
    if (rows.length <= PAGE_ROWS) {
      return;
    }
    var pages = Math.ceil(rows.length / PAGE_ROWS);
    var panel = table.closest(".panel");
    var heading = panel ? panel.querySelector("h2") : null;
    var name = heading ? heading.firstChild.textContent.trim() : "the table";

    var nav = document.createElement("nav");
    nav.className = "pager game-pager";
    nav.setAttribute("aria-label", "Pages of " + name);
    var previous = document.createElement("button");
    previous.type = "button";
    previous.className = "button-secondary button-small";
    previous.textContent = "Previous";
    var status = document.createElement("span");
    status.className = "pager-status";
    status.setAttribute("aria-live", "polite");
    var next = document.createElement("button");
    next.type = "button";
    next.className = "button-secondary button-small";
    next.textContent = "Next";
    nav.append(previous, status, next);
    table.closest(".panel-scroll").before(nav);

    var current = 1;
    function show(page) {
      current = page;
      rows.forEach(function (row, index) {
        row.hidden = Math.floor(index / PAGE_ROWS) + 1 !== page;
      });
      var first = (page - 1) * PAGE_ROWS + 1;
      var last = Math.min(page * PAGE_ROWS, rows.length);
      status.textContent =
        "Page " + page + " of " + pages + ", lines " + first + " to " + last + " of " + rows.length;
      previous.disabled = page === 1;
      next.disabled = page === pages;
    }
    previous.addEventListener("click", function () {
      show(current - 1);
    });
    next.addEventListener("click", function () {
      show(current + 1);
    });
    show(1);
  }

  document.addEventListener("DOMContentLoaded", function () {
    setUpLinking();
    startTimer();
    document.querySelectorAll(".game-table").forEach(pageTable);
  });
})();
