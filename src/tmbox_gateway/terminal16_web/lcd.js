/* The 16x2 display as the box draws it: one row per line, one cell per
   character. Shared by the terminal (terminal.js) and the flows page
   (flows-page.js), so a picture of a flow is drawn exactly like the box. */
(function (root) {
  "use strict";
  function drawLCD(lcd, lines) {
    lcd.replaceChildren();
    lcd.setAttribute("aria-label", lines.join(". "));
    for (const line of lines) {
      const row = document.createElement("div"); row.className = "lcd-row";
      for (const character of line.normalize("NFC")) { const cell = document.createElement("span"); cell.className = "lcd-cell"; cell.textContent = character; row.append(cell); }
      lcd.append(row);
    }
  }
  root.TMBoxLCD = {drawLCD};
})(typeof globalThis !== "undefined" ? globalThis : this);
