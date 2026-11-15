// Progressive enhancement for the reconcile page's file drop zones.
//
// Each zone already works without this file: the real file input is
// stretched invisibly over the zone, so a click opens the file picker and a
// dropped file lands in the input. This script adds the conveniences: a
// highlight while a file is dragged over, the chosen file's name, and drop
// handling for browsers that do not drop onto a hidden input by themselves.
(function () {
  "use strict";

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
  });
})();
