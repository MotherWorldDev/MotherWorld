import test from "node:test";
import assert from "node:assert/strict";

import { BoundedBlobCache } from "../frontend/public/js/gpuMemoryPolicy.js";
import {
  cacheEarthImageryProvider,
  EARTH_IMAGE_CACHE_MAX_ACTIVE_JOBS,
} from "../frontend/public/js/earthImageCache.js";

const originalGlobals = {
  Cesium: globalThis.Cesium,
  Image: globalThis.Image,
  URL: globalThis.URL,
};

function installDomMocks({ fetchResults = [], decodeGate = null } = {}) {
  const resources = [];
  const objectUrls = [];
  const revokedUrls = [];
  let imageCount = 0;

  class MockResource {
    constructor(options) {
      this.options = options;
      resources.push(this);
    }

    fetchBlob() {
      return fetchResults.shift();
    }
  }

  class MockImage {
    constructor() {
      this.id = ++imageCount;
      this.decodeCalls = 0;
      this.src = "";
    }

    async decode() {
      this.decodeCalls += 1;
      if (decodeGate) {
        await decodeGate;
      }
    }
  }

  globalThis.Cesium = {
    Resource: MockResource,
    RequestState: { CANCELLED: "cancelled" },
  };
  globalThis.Image = MockImage;
  globalThis.URL = {
    createObjectURL(blob) {
      const url = `blob:mock-${objectUrls.length + 1}`;
      objectUrls.push({ blob, url });
      return url;
    },
    revokeObjectURL(url) {
      revokedUrls.push(url);
    },
  };

  return { resources, objectUrls, revokedUrls, get imageCount() { return imageCount; } };
}

function restoreGlobals() {
  for (const [key, value] of Object.entries(originalGlobals)) {
    if (value === undefined) {
      delete globalThis[key];
    } else {
      globalThis[key] = value;
    }
  }
}

function providerWithCache(cache, urlTemplate = "./assets/earth/{z}/{x}/{y}.jpg") {
  const originalRequestImage = () => {
    throw new Error("original request should not run");
  };
  const provider = { requestImage: originalRequestImage, errorEvent: { marker: true } };
  cacheEarthImageryProvider(provider, { cache, urlTemplate });
  return { provider, originalRequestImage };
}

test.afterEach(restoreGlobals);

test("fetches local XYZ imagery through Cesium.Resource and reuses encoded cache blobs", async () => {
  const blob = { size: 11, marker: "encoded" };
  const mocks = installDomMocks({ fetchResults: [Promise.resolve(blob)] });
  const cache = new BoundedBlobCache(100);
  const { provider } = providerWithCache(cache);
  const request = { id: "scheduler-request" };

  const first = await provider.requestImage(3, 4, 5, request);
  const second = await provider.requestImage(3, 4, 5, request);

  assert.equal(mocks.resources.length, 1);
  assert.deepEqual(mocks.resources[0].options, {
    url: "./assets/earth/5/3/4.jpg",
    request,
  });
  assert.equal(first instanceof globalThis.Image, true);
  assert.equal(second instanceof globalThis.Image, true);
  assert.notStrictEqual(first, second);
  assert.equal(mocks.objectUrls.length, 2);
  assert.deepEqual(mocks.revokedUrls, ["blob:mock-1", "blob:mock-2"]);
  assert.strictEqual(cache.get("./assets/earth/5/3/4.jpg"), blob);
  assert.equal(mocks.imageCount, 2);
});

test("returns undefined for Resource scheduler throttling and retries cleanly", async () => {
  const blob = { size: 3 };
  const mocks = installDomMocks({ fetchResults: [undefined, Promise.resolve(blob)] });
  const cache = new BoundedBlobCache(100);
  const { provider } = providerWithCache(cache);

  assert.equal(provider.requestImage(0, 0, 0, {}), undefined);
  assert.equal(mocks.resources.length, 1);
  const image = await provider.requestImage(0, 0, 0, {});
  assert.equal(image instanceof globalThis.Image, true);
  assert.equal(mocks.resources.length, 2);
});

test("enforces four active decode/fetch jobs per provider", async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const mocks = installDomMocks({
    fetchResults: Array.from({ length: EARTH_IMAGE_CACHE_MAX_ACTIVE_JOBS + 1 }, (_, i) =>
      Promise.resolve({ size: i + 1 })),
    decodeGate: gate,
  });
  const { provider } = providerWithCache(new BoundedBlobCache(100));
  const active = Array.from({ length: EARTH_IMAGE_CACHE_MAX_ACTIVE_JOBS }, (_, i) =>
    provider.requestImage(i, 0, 1, {}));

  assert.equal(provider.requestImage(99, 0, 1, {}), undefined);
  assert.equal(mocks.resources.length, EARTH_IMAGE_CACHE_MAX_ACTIVE_JOBS);
  release();
  await Promise.all(active);
  const retry = await provider.requestImage(99, 0, 1, {});
  assert.equal(retry instanceof globalThis.Image, true);
});

test("cancellation rejects before cache writes and after decoding, always revoking object URLs", async () => {
  const beforeWriteRequest = { cancelled: true };
  const networkBlob = { size: 7 };
  const mocks = installDomMocks({ fetchResults: [Promise.resolve(networkBlob)] });
  const cache = new BoundedBlobCache(100);
  const { provider } = providerWithCache(cache);
  const url = "./assets/earth/2/1/1.jpg";

  await assert.rejects(provider.requestImage(1, 1, 2, beforeWriteRequest), { name: "AbortError" });
  assert.equal(mocks.resources.length, 0);
  assert.equal(cache.get(url), undefined);

  let releaseDecode;
  const decodeGate = new Promise((resolve) => { releaseDecode = resolve; });
  restoreGlobals();
  const decodeMocks = installDomMocks({
    fetchResults: [Promise.resolve(networkBlob)],
    decodeGate,
  });
  const secondCache = new BoundedBlobCache(100);
  const secondProvider = providerWithCache(secondCache).provider;
  const request = { state: "pending" };
  const pending = secondProvider.requestImage(1, 1, 2, request);
  await Promise.resolve();
  request.state = "cancelled";
  releaseDecode();

  await assert.rejects(pending, { name: "AbortError" });
  assert.equal(secondCache.get(url), undefined);
  assert.deepEqual(decodeMocks.revokedUrls, ["blob:mock-1"]);
});

test("does not cache a failed decode, so the next request retries the network", async () => {
  const firstBlob = { size: 4, marker: "bad" };
  const secondBlob = { size: 5, marker: "good" };
  const mocks = installDomMocks({ fetchResults: [Promise.resolve(firstBlob), Promise.resolve(secondBlob)] });
  let decodeAttempts = 0;
  globalThis.Image = class RetryImage {
    constructor() {
      this.src = "";
    }

    async decode() {
      decodeAttempts += 1;
      if (decodeAttempts === 1) {
        throw new Error("bad image");
      }
    }
  };

  const cache = new BoundedBlobCache(100);
  const { provider } = providerWithCache(cache);
  await assert.rejects(provider.requestImage(2, 3, 4, {}), /bad image/);
  assert.equal(cache.get("./assets/earth/4/2/3.jpg"), undefined);

  const image = await provider.requestImage(2, 3, 4, {});
  assert.equal(image instanceof globalThis.Image, true);
  assert.equal(mocks.resources.length, 2);
  assert.strictEqual(cache.get("./assets/earth/4/2/3.jpg"), secondBlob);
  assert.deepEqual(mocks.revokedUrls, ["blob:mock-1", "blob:mock-2"]);
});

test("keeps provider metadata and original requests untouched for unsupported templates", () => {
  const original = () => "original";
  const errorEvent = { marker: true };
  const provider = { requestImage: original, errorEvent };
  const returned = cacheEarthImageryProvider(provider, {
    cache: new BoundedBlobCache(100),
    urlTemplate: "https://tiles.example.test/{z}/{x}/{y}.jpg",
  });

  assert.strictEqual(returned, provider);
  assert.strictEqual(provider.requestImage, original);
  assert.strictEqual(provider.errorEvent, errorEvent);
});
