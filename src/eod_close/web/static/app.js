// Progressive enhancement for the reconcile pages. Every page works without
// this file; it only adds conveniences on top of the plain HTML:
//
// - the workspace: selecting a row highlights its partner and shows why they
//   match, partner links switch the phone's list when needed, the rows'
//   reveal is staggered, and focus moves to the result after Reconcile;
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
    workspace.querySelectorAll("[data-select][aria-pressed=true]").forEach(function (button) {
      button.setAttribute("aria-pressed", "false");
    });
    var rows = rowsInGroup(workspace, group);
    if (!rows.length) {
      return;
    }
    rows.forEach(function (row) {
      row.classList.add("is-selected");
      var button = row.querySelector("[data-select]");
      if (button) {
        button.setAttribute("aria-pressed", "true");
      }
    });
    var problem = workspace.querySelector('.problem[data-group="' + group + '"]');
    if (problem) {
      problem.classList.add("is-selected");
    }
    showReason(workspace, rows);
  }

  // "B02 ↔ L02 · Fee: Arrived 1 working day later…", in the live region, so
  // screen readers hear the same line sighted users see.
  function showReason(workspace, rows) {
    var text = workspace.querySelector("[data-selection-text]");
    if (!text) {
      return;
    }
    var ids = function (side) {
      return rows
        .filter(function (row) {
          return row.id.indexOf("row-" + side + "-") === 0;
        })
        .map(function (row) {
          return row.querySelector(".row-id").textContent;
        })
        .join(", ");
    };
    var bank = ids("bank");
    var ledger = ids("ledger");
    var who = [bank, ledger].filter(Boolean).join(" ↔ ");
    var strong = document.createElement("strong");
    strong.textContent = who + " · " + rows[0].dataset.tag + ": ";
    text.replaceChildren(strong, document.createTextNode(rows[0].dataset.reason));
  }

  // On a phone only one list shows at a time; following a link to the other
  // list switches it first, so the highlighted partner is visible.
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
    var tag = event.target.closest("[data-select]");
    if (tag) {
      select(workspace, tag.closest(".row").dataset.group);
      return;
    }
    var fromPanel = event.target.closest("[data-select-group]");
    if (fromPanel) {
      var group = fromPanel.dataset.selectGroup;
      select(workspace, group);
      var first = rowsInGroup(workspace, group)[0];
      if (first) {
        showSide(workspace, first.id.indexOf("row-ledger-") === 0 ? "ledger" : "bank");
        first.scrollIntoView({ block: "center", behavior: smoothOrNot() });
        var button = first.querySelector("[data-select]");
        if (button) {
          button.focus({ preventScroll: true });
        }
      }
      return;
    }
    var partner = event.target.closest(".partner");
    if (partner) {
      showSide(workspace, partner.dataset.partnerSide);
      select(workspace, partner.closest(".row").dataset.group);
      return;
    }
    // A click anywhere else on a row selects it too, for pointer users.
    var row = event.target.closest(".row[data-group]");
    if (row && !event.target.closest("a, button, input, label")) {
      select(workspace, row.dataset.group);
    }
  });

  function smoothOrNot() {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
  }

  // After HTMX swaps in the reconciled workspace, set up its rows and move
  // focus to the status line, so keyboard and screen-reader users land on
  // the result rather than on a button that no longer exists.
  document.addEventListener("htmx:afterSwap", function (event) {
    var workspace = event.target.querySelector("[data-workspace]");
    if (!workspace) {
      return;
    }
    prepareWorkspace(workspace);
    var status = workspace.querySelector("#status-line");
    if (status) {
      status.focus();
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
    // Only a reconciled workspace has a reveal to stagger (reached without
    // HTMX when the form posts as a plain page).
    document.querySelectorAll("[data-workspace].is-reconciled").forEach(prepareWorkspace);
  });
})();
