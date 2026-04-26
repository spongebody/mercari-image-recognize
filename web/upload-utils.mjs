export const DEFAULT_UPLOAD_OPTIONS = {
  maxDimension: 1600,
  outputType: "image/jpeg",
  quality: 0.82,
  maxTotalBytes: 10 * 1024 * 1024,
};

export function calculateResizeDimensions(width, height, maxDimension) {
  if (!width || !height || !maxDimension || Math.max(width, height) <= maxDimension) {
    return { width, height };
  }

  const scale = maxDimension / Math.max(width, height);
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

function extensionForMimeType(mimeType) {
  if (mimeType === "image/webp") return "webp";
  if (mimeType === "image/png") return "png";
  return "jpg";
}

export function buildUploadFileName(originalName, mimeType) {
  const extension = extensionForMimeType(mimeType);
  const baseName = (originalName || "image").replace(/\.[^.]*$/, "") || "image";
  return `${baseName}-upload.${extension}`;
}

export function prepareUploadSelection(selectedFiles, options = {}) {
  const maxTotalBytes = options.maxTotalBytes ?? DEFAULT_UPLOAD_OPTIONS.maxTotalBytes;
  const files = selectedFiles.map((item) => item.uploadFile || item.file);
  const totalOriginalBytes = selectedFiles.reduce((sum, item) => sum + (item.file?.size || 0), 0);
  const totalUploadBytes = files.reduce((sum, file) => sum + (file?.size || 0), 0);

  if (totalUploadBytes > maxTotalBytes) {
    throw new Error(
      `Upload payload is too large: ${totalUploadBytes} bytes exceeds ${maxTotalBytes} bytes.`,
    );
  }

  return {
    files,
    items: selectedFiles,
    totalOriginalBytes,
    totalUploadBytes,
    savedBytes: Math.max(0, totalOriginalBytes - totalUploadBytes),
  };
}

function readImage(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(url);
      resolve(image);
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error(`Failed to load image: ${file.name}`));
    };
    image.src = url;
  });
}

function canvasToBlob(canvas, type, quality) {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (blob) {
          resolve(blob);
        } else {
          reject(new Error("Failed to compress image."));
        }
      },
      type,
      quality,
    );
  });
}

export async function compressImageFile(file, options = {}) {
  const settings = { ...DEFAULT_UPLOAD_OPTIONS, ...options };
  if (!file?.type?.startsWith("image/") || file.type === "image/svg+xml") {
    return file;
  }

  const image = await readImage(file);
  const dimensions = calculateResizeDimensions(
    image.naturalWidth || image.width,
    image.naturalHeight || image.height,
    settings.maxDimension,
  );

  const canvas = document.createElement("canvas");
  canvas.width = dimensions.width;
  canvas.height = dimensions.height;

  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, dimensions.width, dimensions.height);
  ctx.drawImage(image, 0, 0, dimensions.width, dimensions.height);

  const blob = await canvasToBlob(canvas, settings.outputType, settings.quality);
  if (!blob || blob.size <= 0) {
    return file;
  }

  const uploadName = buildUploadFileName(file.name, settings.outputType);
  return new File([blob], uploadName, {
    type: settings.outputType,
    lastModified: file.lastModified || Date.now(),
  });
}

export async function prepareCompressedUploadSelection(selectedFiles, options = {}) {
  const items = await Promise.all(
    selectedFiles.map(async (item) => ({
      ...item,
      uploadFile: await compressImageFile(item.file, options),
    })),
  );

  return prepareUploadSelection(items, options);
}
