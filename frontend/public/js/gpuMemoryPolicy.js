/**
 * Side effect free memory policy helpers for GPU heavy views.
 *
 * The cache stores encoded Blobs returned by fetch. Keeping decoded image data
 * in this cache would make the byte budget impossible to reason about because
 * the browser's decoded representation is not exposed.
 */

const MEBIBYTE = 1024 * 1024;
const MOBILE_VIEWPORT_MAX = 768;

const MOBILE_LIMITS = Object.freeze({
  celestialTileLimit: 24,
  inactiveTTL: 0,
  encodedBlobBudget: 16 * MEBIBYTE,
  maxConcurrent: 2,
  globeTileCacheSize: 24,
});

const DESKTOP_LIMITS = Object.freeze({
  celestialTileLimit: 48,
  inactiveTTL: 15_000,
  encodedBlobBudget: 16 * MEBIBYTE,
  maxConcurrent: 4,
  globeTileCacheSize: 1_200,
});

function readViewportWidth(options, nav) {
  if (typeof options.width === "function") {
    return Number(options.width());
  }
  if (Number.isFinite(options.width)) {
    return Number(options.width);
  }

  // Read lazily so importing this module never touches a browser global.
  if (nav && Number.isFinite(nav.innerWidth)) {
    return Number(nav.innerWidth);
  }
  if (typeof globalThis !== "undefined" && Number.isFinite(globalThis.innerWidth)) {
    return Number(globalThis.innerWidth);
  }
  return NaN;
}

function readMatchMedia(options, nav) {
  if (typeof options.matchMedia === "function") {
    return options.matchMedia;
  }
  if (nav && typeof nav.matchMedia === "function") {
    return nav.matchMedia.bind(nav);
  }
  if (typeof globalThis !== "undefined" && typeof globalThis.matchMedia === "function") {
    return globalThis.matchMedia.bind(globalThis);
  }
  return null;
}

function hasCoarsePointer(options, nav) {
  const matchMedia = readMatchMedia(options, nav);
  if (!matchMedia) {
    return false;
  }

  try {
    return Boolean(matchMedia("(pointer: coarse)")?.matches);
  } catch {
    // User-agent signals still provide a useful answer if an injected
    // matchMedia implementation rejects this query.
    return false;
  }
}

/**
 * Return whether the supplied environment should use the mobile GPU policy.
 * All inputs are injectable so this function remains deterministic in tests
 * and does not require a DOM.
 */
export function isMobileDevice(options = {}) {
  const nav = options.navigator ?? (typeof globalThis !== "undefined" ? globalThis.navigator : undefined);
  if (nav?.userAgentData?.mobile === true) {
    return true;
  }

  const userAgent = String(nav?.userAgent ?? "");
  if (/Android|iPhone|iPad|iPod/i.test(userAgent)) {
    return true;
  }

  // iPadOS reports itself as desktop Safari (MacIntel), but retains multiple
  // touch points. A normal desktop touchscreen does not satisfy this signal.
  if (nav?.platform === "MacIntel" && Number(nav?.maxTouchPoints) > 1) {
    return true;
  }

  // Pointer capability alone is insufficient: a large touchscreen desktop
  // must continue using desktop limits.
  const width = readViewportWidth(options, nav);
  return hasCoarsePointer(options, nav) && Number.isFinite(width) && width <= MOBILE_VIEWPORT_MAX;
}

/** Return GPU and encoded-asset limits for the current environment. */
export function getGpuMemoryPolicy(options = {}) {
  const mobile = isMobileDevice(options);
  return {
    mobile,
    ...(mobile ? MOBILE_LIMITS : DESKTOP_LIMITS),
  };
}

function blobByteLength(blob) {
  if (blob == null) {
    return null;
  }
  if (Number.isFinite(blob.size)) {
    const bytes = Number(blob.size);
    return bytes > 0 ? Math.floor(bytes) : null;
  }
  // Keep the class convenient to exercise with test doubles.
  if (Number.isFinite(blob.byteLength)) {
    const bytes = Number(blob.byteLength);
    return bytes > 0 ? Math.floor(bytes) : null;
  }
  return null;
}

/**
 * Insertion-ordered, byte-bounded LRU for encoded Blobs.
 *
 * Values are addressed by URL. `get` refreshes recency and `set` evicts the
 * oldest entries until the complete cache fits in the configured budget.
 */
export class BoundedBlobCache {
  #budget;
  #entries = new Map();
  #byteLength = 0;

  constructor(byteBudget = MOBILE_LIMITS.encodedBlobBudget) {
    if (!Number.isFinite(byteBudget) || byteBudget < 0) {
      throw new TypeError("byteBudget must be a finite non-negative number");
    }
    this.#budget = Math.floor(byteBudget);
  }

  get(url) {
    if (!this.#entries.has(url)) {
      return undefined;
    }

    const entry = this.#entries.get(url);
    // Map iteration order is insertion order. Delete/reinsert touches the
    // entry without changing the Blob or counting it twice.
    this.#entries.delete(url);
    this.#entries.set(url, entry);
    return entry.compressedBlob;
  }

  set(url, blob) {
    const bytes = blobByteLength(blob);
    // Empty/invalid responses and a zero-budget cache are never cacheable.
    // Check before removing an existing value so an invalid replacement is a
    // true no-op for an already cached URL.
    if (bytes == null || this.#budget === 0 || bytes > this.#budget) {
      return false;
    }

    const previous = this.#entries.get(url);
    if (previous) {
      this.#entries.delete(url);
      this.#byteLength -= previous.bytes;
    }

    while (this.#byteLength + bytes > this.#budget && this.#entries.size > 0) {
      const oldestUrl = this.#entries.keys().next().value;
      const oldest = this.#entries.get(oldestUrl);
      this.#entries.delete(oldestUrl);
      this.#byteLength -= oldest.bytes;
    }

    this.#entries.set(url, { compressedBlob: blob, bytes });
    this.#byteLength += bytes;
    return true;
  }

  clear() {
    this.#entries.clear();
    this.#byteLength = 0;
  }

  get byteLength() {
    return this.#byteLength;
  }

  get size() {
    return this.#entries.size;
  }
}

export const GPU_MEMORY_LIMITS = Object.freeze({
  mobile: MOBILE_LIMITS,
  desktop: DESKTOP_LIMITS,
});
