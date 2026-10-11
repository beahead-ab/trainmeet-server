/* The 16x2 display as the box draws it: one row per line, one cell per
   character. Shared by the terminal (terminal.js) and the flows page
   (flows-page.js), so a picture of a flow is drawn exactly like the box. */
(function (root) {
  "use strict";
  function drawLCD(lcd, lines) {
    lcd.replaceChildren();
    lcd.setAttribute("aria-label", lines.join(". "));
    // 16 cells, always drawn by the style sheet the case look is locked to; a
    // box that announced a 20x4 widens the grid and shrinks the cells to fit.
    const columns = Math.max(16, ...lines.map(line => line.normalize("NFC").length));
    for (const line of lines) {
      const row = document.createElement("div"); row.className = "lcd-row";
      if (columns > 16) row.style.gridTemplateColumns = `repeat(${columns}, minmax(0, 1fr))`;
      for (const character of line.normalize("NFC")) {
        const cell = document.createElement("span"); cell.className = "lcd-cell"; cell.textContent = character;
        if (columns > 16) cell.style.fontSize = `clamp(10px, ${(128 / columns).toFixed(2)}cqi, 19px)`;
        row.append(cell);
      }
      lcd.append(row);
    }
  }
  root.TMBoxLCD = {drawLCD};
})(typeof globalThis !== "undefined" ? globalThis : this);
