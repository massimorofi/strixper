// Client-side mirror of the backend upload rules.
//
// The backend is authoritative and will reject anything non-compliant; this
// exists so the user sees "that file is too big" before the round trip
// rather than after. The numbers are defaults and are overridden by the
// values the backend reports from /api/v1/config when available.

export const DEFAULT_LIMITS = {
  maxImageSizeMb: 8,
  maxFileSizeMb: 50,
  maxImagesPerMessage: 4,
  allowedImageTypes: ['image/png', 'image/jpeg', 'image/webp', 'image/gif'],
  allowedFileTypes: ['*'],
}

const IMAGE_EXT_RE = /\.(png|jpe?g|webp|gif|bmp)$/i

export const IMAGE_MIME_BY_EXT = {
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.gif': 'image/gif',
  '.bmp': 'image/bmp',
}

export function extOf(name) {
  const i = (name || '').lastIndexOf('.')
  return i > 0 ? name.slice(i).toLowerCase() : ''
}

export function isImageName(name) {
  return IMAGE_EXT_RE.test(name || '')
}

export function isImageFile(file) {
  if (!file) return false
  if (file.type && file.type.startsWith('image/')) return true
  return isImageName(file.name)
}

export function formatBytes(bytes) {
  const n = Number(bytes)
  if (!Number.isFinite(n) || n < 0) return '0 B'
  if (n < 1024) return `${n} B`
  const units = ['KiB', 'MiB', 'GiB', 'TiB']
  let value = n / 1024
  let idx = 0
  while (value >= 1024 && idx < units.length - 1) {
    value /= 1024
    idx += 1
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[idx]}`
}

// Merge the backend-reported limits over the defaults. Anything missing or
// of the wrong shape is ignored so a partial /config response cannot leave
// the client with a broken validator.
export function resolveLimits(config) {
  const out = { ...DEFAULT_LIMITS }
  if (!config || typeof config !== 'object') return out
  const num = (key, target) => {
    const v = Number(config?.[key])
    if (Number.isFinite(v) && v > 0) out[target] = v
  }
  num('max_image_size_mb', 'maxImageSizeMb')
  num('max_file_size_mb', 'maxFileSizeMb')
  num('max_images_per_message', 'maxImagesPerMessage')
  if (Array.isArray(config?.allowed_image_types) && config.allowed_image_types.length) {
    out.allowedImageTypes = config.allowed_image_types
  }
  if (Array.isArray(config?.allowed_file_types) && config.allowed_file_types.length) {
    out.allowedFileTypes = config.allowed_file_types
  }
  return out
}

function typeAllowed(mime, allowed) {
  if (!Array.isArray(allowed) || allowed.length === 0) return true
  if (allowed.includes('*')) return true
  if (allowed.includes(mime)) return true
  return allowed.some(
    (a) => typeof a === 'string' && a.endsWith('/*') && mime.startsWith(a.slice(0, -1)),
  )
}

// Returns null when the file is acceptable, otherwise a human-readable
// reason. Checks cheap things first (size) so the common rejection is the
// fastest one.
export function validateFile(file, limits = DEFAULT_LIMITS, { imageCount = 0 } = {}) {
  if (!file) return 'no file selected'

  const maxFileBytes = limits.maxFileSizeMb * 1024 * 1024
  if (file.size > maxFileBytes) {
    return `${file.name} is ${formatBytes(file.size)}; the limit is ${formatBytes(maxFileBytes)}`
  }
  if (file.size === 0) return `${file.name} is empty`

  if (isImageFile(file)) {
    const maxImageBytes = limits.maxImageSizeMb * 1024 * 1024
    if (file.size > maxImageBytes) {
      return `${file.name} is ${formatBytes(file.size)}; images are limited to ${formatBytes(maxImageBytes)}`
    }
    const mime = file.type || IMAGE_MIME_BY_EXT[extOf(file.name)] || 'image/png'
    if (!typeAllowed(mime, limits.allowedImageTypes)) {
      return `${mime} images are not accepted`
    }
    if (imageCount >= limits.maxImagesPerMessage) {
      return `at most ${limits.maxImagesPerMessage} images per message`
    }
    return null
  }

  if (!typeAllowed(file.type || '', limits.allowedFileTypes)) {
    const ext = extOf(file.name)
    if (!typeAllowed(ext, limits.allowedFileTypes)) {
      return `${ext || 'unknown'} files are not accepted`
    }
  }
  return null
}

// Pick the image files out of a FileList / array.
export function splitImagesAndFiles(files) {
  const list = Array.from(files || [])
  return {
    images: list.filter(isImageFile),
    files: list.filter((f) => !isImageFile(f)),
  }
}

// Turn a stored record into the attachment shape the chat/agent endpoints
// expect. Kept in one place so both send paths agree.
export function toAttachment(record, kind) {
  return {
    type: kind || (isImageName(record.name) ? 'image' : 'file'),
    file_id: record.file_id || record.id,
    name: record.name || '',
    size: record.size || 0,
    mime_type: record.mime_type || 'application/octet-stream',
    workspace_path: record.workspace_path || record.path || null,
  }
}

export function fileKindLabel(mimeType) {
  if (!mimeType) return 'file'
  if (mimeType.startsWith('image/')) return 'image'
  if (mimeType.startsWith('text/')) return 'text'
  if (mimeType === 'application/json') return 'JSON'
  if (mimeType === 'application/pdf') return 'PDF'
  if (mimeType === 'application/zip') return 'archive'
  return mimeType
}
