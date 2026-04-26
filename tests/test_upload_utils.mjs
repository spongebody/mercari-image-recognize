import assert from "node:assert/strict";
import {
  buildUploadFileName,
  calculateResizeDimensions,
  prepareUploadSelection,
  resolveCompressionThresholdBytes,
  shouldCompressFile,
} from "../web/upload-utils.mjs";

function file(name, size) {
  return { name, size, type: "image/png" };
}

{
  const resized = calculateResizeDimensions(4032, 3024, 1600);
  assert.deepEqual(resized, { width: 1600, height: 1200 });
}

{
  const resized = calculateResizeDimensions(900, 600, 1600);
  assert.deepEqual(resized, { width: 900, height: 600 });
}

{
  const prepared = prepareUploadSelection(
    [
      { file: file("front.png", 3_000_000), uploadFile: file("front-upload.jpg", 700_000) },
      { file: file("back.png", 2_500_000), uploadFile: file("back-upload.jpg", 600_000) },
    ],
    { maxTotalBytes: 10_000_000 },
  );

  assert.equal(prepared.totalOriginalBytes, 5_500_000);
  assert.equal(prepared.totalUploadBytes, 1_300_000);
  assert.equal(prepared.files[0].name, "front-upload.jpg");
  assert.equal(prepared.files[1].name, "back-upload.jpg");
}

{
  assert.throws(
    () =>
      prepareUploadSelection(
        [
          { file: file("a.png", 6_000_000), uploadFile: file("a-upload.jpg", 6_000_000) },
          { file: file("b.png", 5_000_001), uploadFile: file("b-upload.jpg", 5_000_001) },
        ],
        { maxTotalBytes: 10_000_000 },
      ),
    /too large/i,
  );
}

{
  assert.equal(buildUploadFileName("IMG_001.PNG", "image/jpeg"), "IMG_001-upload.jpg");
  assert.equal(buildUploadFileName("photo", "image/webp"), "photo-upload.webp");
}

{
  assert.equal(shouldCompressFile(file("small.png", 900_000), 1_000_000), false);
  assert.equal(shouldCompressFile(file("large.png", 1_000_001), 1_000_000), true);
}

{
  assert.equal(resolveCompressionThresholdBytes("1"), 1_048_576);
  assert.equal(resolveCompressionThresholdBytes("2.5"), 2_621_440);
  assert.equal(resolveCompressionThresholdBytes("0"), 0);
  assert.equal(resolveCompressionThresholdBytes("bad"), 1_048_576);
}
