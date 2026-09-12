/** Progress reflects startup stages, not a guessed download byte count. */
export function waitForInitialView(globe, { doc = document, now = () => performance.now(), pollMs = 100, stableMs = 700, offerContinueMs = 15000 } = {}) {
  const progress = doc.getElementById('startup-progress');
  const skip = doc.getElementById('startup-continue');
  const status = doc.getElementById('loading-text');
  progress.value = 55;
  status.textContent = 'Loading the first view…';
  return new Promise(resolve => {
    const started = now();
    let stableSince = null;
    const finish = () => {
      clearInterval(timer);
      skip.removeEventListener('click', finish);
      skip.hidden = true;
      progress.value = 100;
      resolve();
    };
    const timer = setInterval(() => {
      const state = globe.getInitialViewState();
      progress.value = Math.max(progress.value, 55 + (state.earthReady ? 25 : 0) + (state.skyReady ? 15 : 0));
      if (state.earthReady && state.skyReady && !doc.hidden) {
        if (stableSince === null) stableSince = now();
        if (now() - stableSince >= stableMs) { finish(); return; }
      } else stableSince = null;
      if (now() - started >= offerContinueMs) skip.hidden = false;
      globe.viewer.scene.requestRender();
    }, pollMs);
    skip.addEventListener('click', finish);
  });
}
