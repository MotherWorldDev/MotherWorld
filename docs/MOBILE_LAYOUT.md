# Mobile layout and optional performance panel

Released 2026-09-09.

- At widths up to 980px, map actions live in the Controls menu. Choosing an action closes the menu.
- Selected region details use a bottom panel with independently scrolling content and horizontal section tabs. Short landscape screens use a side panel with vertical tabs.
- Dynamic viewport sizing and safe-area spacing support mobile browser chrome. Touch controls have larger targets; search fields use 16px text to avoid focus zoom.
- Performance statistics are hidden on every fresh page load. Open Controls → Performance on mobile, or Performance in the desktop toolbar. Close with × or Escape. The latest report is retained while hidden without updating its text each frame.
- Sky controls also have a close button. Optional controls preserve keyboard focus when dismissed.

Implementation: `frontend/public/styles.css`, `frontend/public/js/chromeControls.js`, and the performance renderer in `frontend/public/js/main.js`.

Validation: all 57 JavaScript tests passed. Headless Edge checks exercised the actual HTML, CSS, UI summary renderer and control handlers at 320×568, 390×844, 768×1024, 844×390 and 1440×900. Checked page and panel bounds, scrolling room, initial hidden state, performance open/close, sky controls and Escape. Reviewed phone and landscape screenshots. These layout checks did not initialize Cesium or emulate physical iOS/Android hardware.
