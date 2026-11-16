// Progressive enhancement for the reconcile pages. Every page works without
// this file; it only adds conveniences on top of the plain HTML:
//
// - the workspace: selecting a problem card highlights its rows in both
//   lists (showing every row and switching the phone's list if needed),
//   clicking a matched row highlights its partner, the rows' reveal is
//   staggered, and focus moves to the headline after Match;
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

  function prepareWorkspace(workspace) {
    // Each row's position drives its place in the staggered reveal.
    workspace.querySelectorAll(".lists tbody").forEach(function (body) {
      Array.prototype.forEach.call(body.rows, function (row, index) {
        row.style.setProperty("--i", String(index));
      });
    });
  }

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

  // After HTMX swaps in the matched workspace, set up its rows and move
  // focus to the headline, so keyboard and screen-reader users land on the
  // result rather than on a button that no longer exists. This waits for
  // the settle step, and the headline has an ID of its own, because HTMX
  // briefly gives a swapped element the old attributes of any element that
  // shared its ID, which would remove the headline's tabindex.
  document.addEventListener("htmx:afterSettle", function (event) {
    var workspace = event.target.querySelector("[data-workspace]");
    if (!workspace) {
      return;
    }
    prepareWorkspace(workspace);
    var headline = workspace.querySelector(".result-headline");
    if (headline) {
      headline.focus();
    }
  });

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
    // Only a matched workspace has a reveal to stagger (reached without
    // HTMX when the form posts as a plain page).
    document.querySelectorAll("[data-workspace].is-reconciled").forEach(prepareWorkspace);
  });
})();
