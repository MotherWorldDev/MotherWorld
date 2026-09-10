import { BoundedBlobCache, getGpuMemoryPolicy } from "./gpuMemoryPolicy.js?v=20260910-memory1";

const MAX_ACTIVE_JOBS = 4;
const TEMPLATE_TOKEN = /\{[^{}]*\}/g;
const ALLOWED_TOKENS = new Set(["{z}", "{x}", "{y}"]);

function isSimpleLocalUrlTemplate(value) {
  if (typeof value !== "string" || value.length === 0) {
    return false;
  }

  // Resource is intentionally used only for bundled/local imagery.  This
  // keeps the wrapper from changing the authentication, subdomain, or retry
  // behavior of a provider that was configured for a remote service.
  if (/^[a-z][a-z\d+.-]*:/i.test(value) || value.startsWith("//")) {
    return false;
  }

  const tokens = value.match(TEMPLATE_TOKEN) || [];
  if (!tokens.includes("{z}") || !tokens.includes("{x}") || !tokens.includes("{y}")) {
    return false;
  }
  return tokens.every((token) => ALLOWED_TOKENS.has(token));
}

function expandUrlTemplate(urlTemplate, x, y, level) {
  return urlTemplate
    .replaceAll("{z}", String(level))
    .replaceAll("{x}", String(x))
    .replaceAll("{y}", String(y));
}

function getCesium() {
  return typeof globalThis !== "undefined" ? globalThis.Cesium : undefined;
}

function isRequestCancelled(request, Cesium) {
  if (!request) {
    return false;
  }
  if (Boolean(request.cancelled)) {
    return true;
  }
  const cancelledState = Cesium?.RequestState?.CANCELLED;
  return cancelledState !== undefined && request.state === cancelledState;
}

function createAbortError() {
  if (typeof DOMException === "function") {
    return new DOMException("The imagery request was cancelled", "AbortError");
  }
  const error = new Error("The imagery request was cancelled");
  error.name = "AbortError";
  return error;
}

function clearDecodedImage(image) {
  if (!image) {
    return;
  }
  try {
    // Releasing the source on failed/cancelled decodes avoids keeping a
    // browser-side decoded surface alive after the wrapper rejects.
    image.src = "";
  } catch {
    // Small test doubles and host objects are allowed to expose a read-only
    // src property.  There is no cleanup to perform in that case.
  }
}

function createDefaultCache() {
  return new BoundedBlobCache(getGpuMemoryPolicy().encodedBlobBudget);
}

function isBlobCache(value) {
  return value && typeof value.get === "function" && typeof value.set === "function";
}

function createResource(Cesium, url, request) {
  const Resource = Cesium?.Resource;
  if (typeof Resource !== "function") {
    return null;
  }

  const options = { url, request };
  try {
    // Cesium.Resource is a constructor in CesiumJS.  The callable fallback
    // also keeps the wrapper friendly to lightweight factory mocks.
    return new Resource(options);
  } catch (error) {
    if (!(error instanceof TypeError)) {
      throw error;
    }
    return Resource(options);
  }
}

async function decodeBlob(blob, request, Cesium) {
  if (isRequestCancelled(request, Cesium)) {
    throw createAbortError();
  }

  const URLObject = globalThis?.URL;
  const ImageConstructor = globalThis?.Image;
  if (!URLObject || typeof URLObject.createObjectURL !== "function" ||
      typeof URLObject.revokeObjectURL !== "function") {
    throw new Error("URL.createObjectURL is unavailable");
  }
  if (typeof ImageConstructor !== "function") {
    throw new Error("Image is unavailable");
  }

  const objectUrl = URLObject.createObjectURL(blob);
  let image;
  try {
    image = new ImageConstructor();
    image.src = objectUrl;
    if (typeof image.decode === "function") {
      await image.decode();
    } else {
      // All supported browsers expose decode(), but a load fallback makes
      // this adapter usable with older DOMs and minimal test doubles.
      await new Promise((resolve, reject) => {
        image.onload = resolve;
        image.onerror = () => reject(new Error("Unable to decode imagery"));
      });
    }

    if (isRequestCancelled(request, Cesium)) {
      clearDecodedImage(image);
      throw createAbortError();
    }
    return image;
  } catch (error) {
    clearDecodedImage(image);
    throw error;
  } finally {
    URLObject.revokeObjectURL(objectUrl);
  }
}

/**
 * Wrap a Cesium imagery provider that uses a simple local XYZ URL template.
 *
 * The wrapper caches encoded Blobs while creating a fresh HTMLImageElement on
 * every successful call.  It returns Cesium's usual undefined throttle value
 * synchronously when either the provider gate or Resource scheduler declines
 * a job, allowing Cesium to retry the tile on a later frame.
 */
export function cacheEarthImageryProvider(provider, options = {}) {
  const { cache: requestedCache, urlTemplate } = options || {};
  if (!provider || !isSimpleLocalUrlTemplate(urlTemplate)) {
    return provider;
  }

  const cache = isBlobCache(requestedCache) ? requestedCache : createDefaultCache();
  const originalRequestImage = provider.requestImage;
  let activeJobs = 0;

  provider.requestImage = function cachedRequestImage(x, y, level, request) {
    const Cesium = getCesium();
    if (isRequestCancelled(request, Cesium)) {
      return Promise.reject(createAbortError());
    }

    if (activeJobs >= MAX_ACTIVE_JOBS) {
      return undefined;
    }

    // If Cesium is not present yet, leave an otherwise valid provider usable.
    // This also preserves the provider's original request implementation as
    // the fallback for environments that cannot construct a Resource.
    if (typeof Cesium?.Resource !== "function") {
      return typeof originalRequestImage === "function"
        ? originalRequestImage.call(provider, x, y, level, request)
        : undefined;
    }

    const url = expandUrlTemplate(urlTemplate, x, y, level);
    activeJobs += 1;

    let cachedBlob;
    try {
      cachedBlob = cache.get(url);
    } catch (error) {
      activeJobs -= 1;
      return Promise.reject(error);
    }

    let work;
    if (cachedBlob !== undefined && cachedBlob !== null) {
      work = Promise.resolve().then(() => decodeBlob(cachedBlob, request, Cesium));
    } else {
      let resource;
      try {
        resource = createResource(Cesium, url, request);
        if (!resource || typeof resource.fetchBlob !== "function") {
          throw new Error("Cesium.Resource.fetchBlob is unavailable");
        }
        const fetchResult = resource.fetchBlob();
        if (fetchResult === undefined) {
          activeJobs -= 1;
          return undefined;
        }
        work = Promise.resolve(fetchResult).then((blob) => {
          // A scheduler can finish a request after the caller has cancelled
          // it.  Do this check before the encoded cache write and decode.
          if (blob === undefined || blob === null) {
            return undefined;
          }
          if (isRequestCancelled(request, Cesium)) {
            throw createAbortError();
          }
          if (isRequestCancelled(request, Cesium)) {
            throw createAbortError();
          }
          return decodeBlob(blob, request, Cesium).then((image) => {
            // Cache only an image that decoded successfully.  A rejected
            // decode must remain retryable and must not poison the Blob LRU.
            if (isRequestCancelled(request, Cesium)) {
              clearDecodedImage(image);
              throw createAbortError();
            }
            cache.set(url, blob);
            return image;
          });
        });
      } catch (error) {
        activeJobs -= 1;
        return Promise.reject(error);
      }
    }

    return work.finally(() => {
      activeJobs -= 1;
    });
  };

  return provider;
}

export const EARTH_IMAGE_CACHE_MAX_ACTIVE_JOBS = MAX_ACTIVE_JOBS;
