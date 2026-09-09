/** Keep optional map controls out of the way on small screens. */
export function createChromeControls({ doc = document, win = window, onPerfOpen = () => {} } = {}) {
  const byId = (id) => doc.getElementById(id);
  const menuButton = byId("map-controls-toggle-btn");
  const menu = byId("map-controls");
  const header = menuButton.closest("header");
  const mobile = win.matchMedia("(max-width: 980px)");
  const perf = byId("perf-hud");
  const perfButton = byId("perf-toggle-btn");
  const sky = byId("sky-controls-panel");
  const skyButton = byId("sky-controls-toggle-btn");
  let menuOpen = false;

  function setMenu(open) {
    menuOpen = mobile.matches && open;
    header.classList.toggle("controls-open", menuOpen);
    menuButton.setAttribute("aria-expanded", String(menuOpen));
    menu.hidden = mobile.matches && !menuOpen;
  }
  function returnFocus(button) {
    (mobile.matches ? menuButton : button).focus();
  }
  function closeSky() {
    sky.hidden = true;
    skyButton.setAttribute("aria-expanded", "false");
  }
  function setPerf(open) {
    perf.hidden = !open;
    perfButton.setAttribute("aria-expanded", String(open));
    if (open) {
      closeSky();
      onPerfOpen();
    }
  }

  menuButton.addEventListener("click", () => {
    const open = !menuOpen;
    if (open) { setPerf(false); closeSky(); }
    setMenu(open);
  });
  perfButton.addEventListener("click", () => {
    const open = perf.hidden;
    setPerf(open);
    setMenu(false);
    if (open) byId("perf-close-btn").focus();
    else returnFocus(perfButton);
  });
  byId("perf-close-btn").addEventListener("click", () => {
    setPerf(false);
    returnFocus(perfButton);
  });
  byId("sky-close-btn").addEventListener("click", () => {
    closeSky();
    returnFocus(skyButton);
  });
  // Existing action listeners run first; then collapse the mobile menu.
  menu.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button || button === perfButton) return;
    if (button === skyButton) setPerf(false);
    if (!mobile.matches) return;
    setMenu(false);
    if (button === skyButton && !sky.hidden) byId("sky-close-btn").focus();
    else menuButton.focus();
  });
  doc.addEventListener("click", (event) => {
    if (!header.contains(event.target)) setMenu(false);
    if (byId("earth-health-root")?.contains(event.target)) {
      setPerf(false);
      closeSky();
    }
  }, true);
  doc.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (!perf.hidden) { setPerf(false); returnFocus(perfButton); }
    else if (!sky.hidden) { closeSky(); returnFocus(skyButton); }
    else if (menuOpen) { setMenu(false); menuButton.focus(); }
  });
  mobile.addEventListener("change", () => {
    const focusedInMenu = menu.contains(doc.activeElement);
    setMenu(false);
    if (mobile.matches && focusedInMenu) menuButton.focus();
    if (!mobile.matches && doc.activeElement === menuButton) byId("reset-view-btn").focus();
  });
  setMenu(false);
  setPerf(false);
  return { closeMenu: () => setMenu(false) };
}
