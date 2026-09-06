import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mediaIdentifierHash, occurrenceImageUrl } from "../frontend/public/js/speciesMedia.js";
test("GBIF image cache keys match the documented example and UTF-8 MD5",()=>{
 const identifier="https://inaturalist-open-data.s3.amazonaws.com/photos/31610070/original.jpg";
 assert.equal(mediaIdentifierHash(identifier),"568639b65b65ddb9090d3d6ef1abce14");
 for(const input of ["", "abc", "životinja 🐟", "x".repeat(150)])assert.equal(mediaIdentifierHash(input),createHash("md5").update(input).digest("hex"));
 assert.equal(occurrenceImageUrl(2005380410,identifier),"https://api.gbif.org/v1/image/cache/640x/occurrence/2005380410/media/568639b65b65ddb9090d3d6ef1abce14");
 assert.equal(occurrenceImageUrl("../1",identifier),null);
});
