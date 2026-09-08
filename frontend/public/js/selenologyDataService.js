export function createSelenologyDataService(config = {}) {
  const url = config.moonSelenologyUrl || "./data/moon/selenology.json";
  let promise = null;
  async function load() {
    if (!promise) {
      promise = fetch(url, { cache: "no-cache" })
        .then(async (response) => {
          if (!response.ok) {
            if (response.status === 404) return null;
            throw new Error(`Selenology index HTTP ${response.status}`);
          }
          return response.json();
        })
        .catch((err) => {
          promise = null;
          throw err;
        });
    }
    return promise;
  }
  return { load };
}
