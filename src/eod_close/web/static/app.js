// Progressive enhancement for the reconcile pages. Every page works without
// this file; it only adds conveniences on top of the plain HTML:
//
// - the workspace: selecting a problem card highlights its rows in both
//   lists (showing every row and switching the phone's list if needed),
//   clicking a matched row highlights its partner, and focus moves to the
//   headline after Match;
// - "Why?" popovers: placed beside their button and kept on screen;
// - the busy day: opening a problem filters the lists to its rows;
// - tooltips: Escape dismisses them (WCAG 1.4.13);
// - upload drop zones: a highlight while dragging and the chosen file's name.
(function () {
  "use strict";

  // ---- Workspace ----------------------------------------------------------

  function rowsInGroup(workspace, group) {
    return Array.prototype.slice.call(
      workspace.querySelectorAll('.row[data-group="' + group + '"]')
    );
  }

  function select(workspace, group) {
    workspace.querySelectorAll(".is-selected").forEach(function (element) {
      element.classList.remove("is-selected");
    });
    workspace.querySelectorAll(".problem[aria-pressed=true]").forEach(function (card) {
      card.setAttribute("aria-pressed", "false");
    });
    rowsInGroup(workspace, group).forEach(function (row) {
      row.classList.add("is-selected");
    });
    var card = workspace.querySelector('.problem[data-select-group="' + group + '"]');
    if (card) {
      card.classList.add("is-selected");
      card.setAttribute("aria-pressed", "true");
    }
  }

  // On a phone only one list shows at a time; selecting a problem switches
  // to the list holding its first row, so the highlight is visible.
  function showSide(workspace, side) {
    var radio = workspace.querySelector("#side-" + side);
    if (radio && !radio.checked) {
      radio.checked = true;
    }
  }

  // The lists keep at least the height they have with every row showing,
  // so choosing Matched or Problems (or the other list, on a phone) hides
  // rows without the rest of the page jumping. The height only ever grows,
  // and is measured again when the window changes size.
  function holdListsHeight(workspace, fresh) {
    var lists = workspace.querySelector(".lists");
    var all = workspace.querySelector("#show-all");
    if (!lists || (all && !all.checked)) {
      return;
    }
    if (fresh) {
      lists.style.minHeight = "";
    }
    var held = parseFloat(lists.style.minHeight) || 0;
    // The exact, fractional height: rounding it would let the page below
    // shift by a pixel when rows are hidden.
    lists.style.minHeight = Math.max(held, lists.getBoundingClientRect().height) + "px";
  }

  function holdAllListHeights(fresh) {
    document.querySelectorAll("[data-workspace]").forEach(function (workspace) {
      holdListsHeight(workspace, fresh);
    });
  }

  document.addEventListener("change", function (event) {
    var workspace = event.target.closest && event.target.closest("[data-workspace]");
    if (workspace && event.target.name === "side") {
      holdListsHeight(workspace, false);
    }
  });

  var resizeTimer = null;
  window.addEventListener("resize", function () {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(function () {
      holdAllListHeights(true);
    }, 150);
  });

  document.addEventListener("click", function (event) {
    var workspace = event.target.closest("[data-workspace]");
    if (!workspace) {
      return;
    }
    var card = event.target.closest("[data-select-group]");
    if (card) {
      var group = card.dataset.selectGroup;
      // A filter showing only matches would hide the rows being pointed at.
      var all = workspace.querySelector("#show-all");
      if (all && !all.checked) {
        all.checked = true;
      }
      select(workspace, group);
      var first = rowsInGroup(workspace, group)[0];
      if (first) {
        showSide(workspace, first.id.indexOf("row-ledger-") === 0 ? "ledger" : "bank");
        first.scrollIntoView({ block: "center", behavior: smoothOrNot() });
      }
      return;
    }
    // A click on a matched row (outside its own controls) highlights its
    // partner, for pointer users comparing the two lists.
    var row = event.target.closest(".row[data-group]");
    if (row && !event.target.closest("a, button, summary, details")) {
      select(workspace, row.dataset.group);
    }
  });

  function smoothOrNot() {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
  }

  // After HTMX swaps in the matched workspace, move focus to the headline,
  // so keyboard and screen-reader users land on the result rather than on
  // a button that no longer exists. This waits for
  // the settle step, and the headline has an ID of its own, because HTMX
  // briefly gives a swapped element the old attributes of any element that
  // shared its ID, which would remove the headline's tabindex.
  document.addEventListener("htmx:afterSettle", function (event) {
    var workspace = event.target.querySelector("[data-workspace]");
    if (!workspace) {
      return;
    }
    holdListsHeight(workspace, true);
    var headline = workspace.querySelector(".result-headline");
    if (headline) {
      headline.focus();
    }
  });

  // ---- "Why?" popovers ---------------------------------------------------
  //
  // The browser's own popover does the work that matters: it opens from its
  // button by click, tap, Enter or Space, closes on Escape or a click
  // outside, keeps only one open, and floats above the table so the table
  // never changes size. This only places it beside its button, flipping
  // above when there is no room below and keeping it inside the page.

  var GAP = 6;
  var MARGIN = 8;

  function invokerOf(popover) {
    return document.querySelector('[popovertarget="' + popover.id + '"]');
  }

  // Before the popover is drawn it has no size, so its widest possible
  // width (the CSS max-width) stands in until it can be measured.
  function place(popover) {
    var button = invokerOf(popover);
    if (!button) {
      return;
    }
    var anchor = button.getBoundingClientRect();
    var width = popover.offsetWidth || Math.min(320, window.innerWidth - 32);
    var height = popover.offsetHeight;
    var left = Math.min(anchor.left, window.innerWidth - width - MARGIN);
    var top = anchor.bottom + GAP;
    if (top + height > window.innerHeight - MARGIN && anchor.top - GAP - height > MARGIN) {
      top = anchor.top - GAP - height;
    }
    popover.style.left = Math.max(MARGIN, left) + "px";
    popover.style.top = Math.max(MARGIN, top) + "px";
  }

  // "beforetoggle" fires before the popover is drawn, so it is placed
  // beside its button there and never shows anywhere else, even for a
  // frame; "toggle" fires a moment later, once it has a size, to keep it
  // on screen.
  document.addEventListener(
    "beforetoggle",
    function (event) {
      if (event.target.classList.contains("why-popover") && event.newState === "open") {
        event.target.setAttribute("data-placed", "");
        place(event.target);
      }
    },
    true
  );
  document.addEventListener(
    "toggle",
    function (event) {
      if (event.target.classList.contains("why-popover") && event.newState === "open") {
        place(event.target);
      }
    },
    true
  );

  // An open popover follows its button when the page or a list scrolls.
  ["scroll", "resize"].forEach(function (type) {
    window.addEventListener(
      type,
      function () {
        document.querySelectorAll(".why-popover:popover-open").forEach(place);
      },
      true
    );
  });

  // ---- Busy day: problem lines -------------------------------------------
  //
  // Opening a problem line shows its next step; only one is open at a time.
  // Its "Show its rows in the lists" link opens the lists (they may be
  // folded away) while HTMX fetches the page holding the problem's rows,
  // which the server marks with is-flash. Once they arrive, they are
  // scrolled into view and the first takes keyboard focus.

  document.addEventListener(
    "toggle",
    function (event) {
      var line = event.target;
      if (!line.classList || !line.classList.contains("problem-line") || !line.open) {
        return;
      }
      document.querySelectorAll(".problem-line[open]").forEach(function (other) {
        if (other !== line) {
          other.open = false;
        }
      });
    },
    true
  );

  document.addEventListener("click", function (event) {
    if (!event.target.closest("[data-show-rows]")) {
      return;
    }
    var fold = document.getElementById("busy-lists-fold");
    if (fold) {
      fold.open = true;
    }
  });

  function bringFlashedRowsIntoView() {
    var first = document.querySelector("#busy-lists .row.is-flash");
    if (!first) {
      return;
    }
    first.setAttribute("tabindex", "-1");
    first.scrollIntoView({ block: "center", behavior: smoothOrNot() });
    first.focus({ preventScroll: true });
  }

  document.addEventListener("htmx:afterSettle", function (event) {
    if (event.target.id === "busy-lists") {
      bringFlashedRowsIntoView();
    }
  });

  // Without JavaScript the link loads the whole page at those rows; with it,
  // a page loaded that way (or reloaded) still lands on them.
  document.addEventListener("DOMContentLoaded", bringFlashedRowsIntoView);

  // ---- Tooltips -----------------------------------------------------------

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") {
      return;
    }
    document.querySelectorAll(".tip").forEach(function (tip) {
      tip.classList.add("is-dismissed");
    });
  });

  // A dismissed tooltip comes back the next time it is hovered or focused.
  ["mouseover", "focusin"].forEach(function (type) {
    document.addEventListener(type, function (event) {
      var tip = event.target.closest && event.target.closest(".tip");
      if (tip && type === "focusin") {
        tip.classList.remove("is-dismissed");
      } else if (!tip) {
        document.querySelectorAll(".tip.is-dismissed").forEach(function (other) {
          other.classList.remove("is-dismissed");
        });
      }
    });
  });

  // ---- Upload drop zones --------------------------------------------------
  //
  // Each zone already works without this: the real file input is stretched
  // invisibly over the zone, so a click opens the picker and a dropped file
  // lands in the input. This adds the highlight while dragging, the chosen
  // file's name, and drop handling for browsers that do not drop onto a
  // hidden input by themselves.

  function showChosen(zone, input) {
    var chosen = zone.querySelector("[data-dropzone-chosen]");
    if (!chosen) {
      return;
    }
    var file = input.files && input.files[0];
    chosen.textContent = file ? "Chosen: " + file.name : "No file chosen";
    zone.classList.toggle("has-file", Boolean(file));
  }

  function setUpZone(zone) {
    var input = zone.querySelector("input[type=file]");
    if (!input) {
      return;
    }
    input.addEventListener("change", function () {
      showChosen(zone, input);
    });

    // dragenter and dragleave fire for every child element, so a counter
    // keeps the highlight steady while the pointer crosses the zone's text.
    var depth = 0;
    zone.addEventListener("dragenter", function (event) {
      event.preventDefault();
      depth += 1;
      zone.classList.add("is-dragover");
    });
    zone.addEventListener("dragover", function (event) {
      // Without this the browser would open the dropped file as a page.
      event.preventDefault();
    });
    zone.addEventListener("dragleave", function () {
      depth = Math.max(0, depth - 1);
      if (depth === 0) {
        zone.classList.remove("is-dragover");
      }
    });
    zone.addEventListener("drop", function (event) {
      event.preventDefault();
      depth = 0;
      zone.classList.remove("is-dragover");
      var files = event.dataTransfer && event.dataTransfer.files;
      if (files && files.length) {
        input.files = files;
        input.dispatchEvent(new Event("change", { bubbles: true }));
      }
    });

    // A file may already be selected after the browser's back button.
    showChosen(zone, input);
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-dropzone]").forEach(setUpZone);
    holdAllListHeights(true);
    // The web fonts can arrive after the page is ready and change the rows'
    // height, so the lists are measured again once they have.
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(function () {
        holdAllListHeights(true);
      });
    }
  });
})();
