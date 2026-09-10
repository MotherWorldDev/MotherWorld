import test from "node:test";
import assert from "node:assert/strict";

import {
  BoundedBlobCache,
  getGpuMemoryPolicy,
} from "../frontend/public/js/gpuMemoryPolicy.js";

const blob = (size, label) => ({ size, label });

test("uses the mobile policy for explicit mobile signals", () => {
  const policy = getGpuMemoryPolicy({
    navigator: {
      userAgentData: { mobile: true },
      userAgent: "Desktop UA",
      platform: "Win32",
      maxTouchPoints: 10,
    },
    width: 1440,
    matchMedia: () => ({ matches: true }),
  });

  assert.deepEqual(policy, {
    mobile: true,
    celestialTileLimit: 24,
    inactiveTTL: 0,
    encodedBlobBudget: 16 * 1024 * 1024,
    maxConcurrent: 2,
    globeTileCacheSize: 24,
  });
});

test("recognizes mobile user agents and iPadOS desktop Safari", () => {
  assert.equal(
    getGpuMemoryPolicy({ navigator: { userAgent: "Mozilla/5.0 (Linux; Android 14)" } }).mobile,
    true,
  );
  assert.equal(
    getGpuMemoryPolicy({ navigator: { userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)" } }).mobile,
    true,
  );
  assert.equal(
    getGpuMemoryPolicy({ navigator: { platform: "MacIntel", maxTouchPoints: 5 } }).mobile,
    true,
  );
});

test("uses coarse pointer only with a small viewport", () => {
  const navigator = { userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" };
  const matchMedia = () => ({ matches: true });

  assert.equal(getGpuMemoryPolicy({ navigator, matchMedia, width: 768 }).mobile, true);
  assert.equal(getGpuMemoryPolicy({ navigator, matchMedia, width: 769 }).mobile, false);

  const desktopTouchNavigator = {
    userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    maxTouchPoints: 10,
  };
  assert.equal(
    getGpuMemoryPolicy({ navigator: desktopTouchNavigator, matchMedia, width: 1920 }).mobile,
    false,
  );
});

test("returns desktop limits when no mobile signal is present", () => {
  assert.deepEqual(
    getGpuMemoryPolicy({
      navigator: { userAgent: "Mozilla/5.0 (X11; Linux x86_64)" },
      width: 1440,
      matchMedia: () => ({ matches: false }),
    }),
    {
      mobile: false,
      celestialTileLimit: 48,
      inactiveTTL: 15_000,
      encodedBlobBudget: 16 * 1024 * 1024,
      maxConcurrent: 4,
      globeTileCacheSize: 1_200,
    },
  );
});

test("BoundedBlobCache evicts the oldest encoded blobs by byte budget", () => {
  const cache = new BoundedBlobCache(10);
  const first = blob(5, "first");
  const second = blob(5, "second");
  const third = blob(5, "third");

  assert.equal(cache.set("/first", first), true);
  assert.equal(cache.set("/second", second), true);
  assert.equal(cache.get("/first"), first); // Touch first; second is oldest.
  assert.equal(cache.set("/third", third), true);

  assert.equal(cache.get("/first"), first);
  assert.equal(cache.get("/second"), undefined);
  assert.equal(cache.get("/third"), third);
  assert.equal(cache.size, 2);
  assert.equal(cache.byteLength, 10);
});

test("skips oversize blobs, accounts for replacements, and clears", () => {
  const cache = new BoundedBlobCache(10);
  const original = blob(4, "original");

  assert.equal(cache.set("/asset", original), true);
  assert.equal(cache.set("/too-large", blob(11, "too-large")), false);
  assert.equal(cache.get("/too-large"), undefined);
  assert.equal(cache.byteLength, 4);
  assert.equal(cache.size, 1);

  const replacement = blob(7, "replacement");
  assert.equal(cache.set("/asset", replacement), true);
  assert.equal(cache.get("/asset"), replacement);
  assert.equal(cache.byteLength, 7);
  assert.equal(cache.size, 1);

  cache.clear();
  assert.equal(cache.byteLength, 0);
  assert.equal(cache.size, 0);
  assert.equal(cache.get("/asset"), undefined);
});

test("rejects empty or invalid blobs without adding zero-byte keys", () => {
  const cache = new BoundedBlobCache(10);
  assert.equal(cache.set("/empty", blob(0, "empty")), false);
  assert.equal(cache.set("/invalid", { label: "invalid" }), false);
  assert.equal(cache.set("/null", null), false);
  assert.equal(cache.size, 0);
  assert.equal(cache.byteLength, 0);

  const zeroBudget = new BoundedBlobCache(0);
  assert.equal(zeroBudget.set("/asset", blob(1, "asset")), false);
  assert.equal(zeroBudget.size, 0);
  assert.equal(zeroBudget.byteLength, 0);
});
