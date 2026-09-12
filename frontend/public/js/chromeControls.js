/** Keep optional map controls out of the way on small screens. */
export function createChromeControls({ doc = document, win = window, onPerfOpen = () => {} } = {}) {
  const byId = (id) => doc.getElementById(id);
  const menuButton = byId("map-controls-toggle-btn");
  const menu = byId("map-controls");
  const header = menuButton.closest(".map-controls-dock");
  const mobile = win.matchMedia("(max-width: 980px)");
  const perf = byId("perf-hud");
  const perfButton = byId("perf-toggle-btn");
  const sky = byId("sky-controls-panel");
  const skyButton = byId("sky-controls-toggle-btn");
  let menuOpen = false;

  function setMenu(open) {
    menuOpen = Boolean(open);
    header.classList.toggle("controls-open", menuOpen);
    menuButton.setAttribute("aria-expanded", String(menuOpen));
    menu.hidden = !menuOpen;
  }
  function returnFocus(button) {
    menuButton.focus();
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
    if (open) { setPerf(false); closeSky(); byId("earth-health-close")?.click(); }
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
    if (!button || button === perfButton || button.id === "landscape-btn") return;
    if (button === skyButton) setPerf(false);
    setMenu(false);
    if (button === skyButton && !sky.hidden) byId("sky-close-btn").focus();
    else menuButton.focus();
  });
  doc.addEventListener("click", (event) => {
    if (!header.contains(event.target)) setMenu(false);
    if (!header.contains(event.target) && byId("earth-health-root")?.contains(event.target)) {
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

  });
  const landscapeButton = byId("landscape-btn");
  const orientationStatus = byId("orientation-status");
  const touchDevice = win.matchMedia("(pointer: coarse)").matches;
  if (landscapeButton && touchDevice) {
    landscapeButton.hidden = false;
    const requestLandscape = async () => {
      setMenu(false);
      try {
        if (!doc.fullscreenElement && doc.documentElement.requestFullscreen) {
          await doc.documentElement.requestFullscreen();
        }
        if (!win.screen.orientation?.lock) throw new Error("unsupported");
        await win.screen.orientation.lock("landscape");
        orientationStatus.hidden = true;
      } catch {
        orientationStatus.textContent = "Rotate your phone to landscape. This browser could not lock the orientation.";
        orientationStatus.hidden = false;
        setMenu(true);
      }
    };
    landscapeButton.addEventListener("click", requestLandscape);
    byId("portrait-landscape-btn")?.addEventListener("click", requestLandscape);
    const portrait = win.matchMedia("(orientation: portrait)");
    const phone = win.matchMedia("(max-width: 600px)");
    const tip = byId("portrait-tip");
    let dismissed = false;
    try { dismissed = win.sessionStorage.getItem("motherworld-portrait-tip-dismissed") === "1"; } catch {}
    const updateTip = () => { if (tip) tip.hidden = dismissed || !portrait.matches || !phone.matches; };
    byId("portrait-tip-close")?.addEventListener("click", () => {
      dismissed = true;
      try { win.sessionStorage.setItem("motherworld-portrait-tip-dismissed", "1"); } catch {}
      updateTip();
    });
    portrait.addEventListener("change", updateTip);
    phone.addEventListener("change", updateTip);
    updateTip();
    // Installed/fullscreen contexts may permit locking without another gesture.
    if (win.screen.orientation?.lock) win.screen.orientation.lock("landscape").catch(() => {});
  }
  setMenu(false);
  setPerf(false);
  return { closeMenu: () => setMenu(false) };
}
