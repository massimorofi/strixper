import { useCallback, useRef, useState } from 'react'
import { uploadFile } from '../api.js'
import { isImageFile, validateFile } from '../utils/fileUtils.js'

// Pending attachments for the composer.
//
// A file is uploaded as soon as it is picked: the backend returns an
// opaque file_id, and everything downstream (rendering, sending,
// persisting) works against that id instead of a blob URL that dies on
// reload. Local preview URLs are kept alongside purely for display.
let seq = 0
const nextKey = () => `f${Date.now().toString(36)}${(seq++).toString(36)}`

export function useFileUpload({ limits, onError } = {}) {
  const [items, setItems] = useState([])
  const itemsRef = useRef(items)
  itemsRef.current = items

  const fail = useCallback(
    (message) => {
      onError?.(message)
      return false
    },
    [onError],
  )

  const addFiles = useCallback(
    async (files) => {
      const incoming = Array.from(files || [])
      if (incoming.length === 0) return []

      const accepted = []
      for (const file of incoming) {
        const imageCount = accepted.filter((f) => isImageFile(f)).length
        const reason = validateFile(file, limits, { imageCount })
        if (reason) {
          fail(reason)
          continue
        }
        accepted.push(file)
      }
      if (accepted.length === 0) return []

      const results = []
      for (const file of accepted) {
        const key = nextKey()
        const previewUrl = isImageFile(file) ? URL.createObjectURL(file) : null
        const pending = {
          key,
          file,
          name: file.name,
          size: file.size,
          mime_type: file.type || 'application/octet-stream',
          isImage: isImageFile(file),
          previewUrl,
          status: 'uploading',
          file_id: null,
          error: null,
        }
        setItems((prev) => [...prev, pending])
        try {
          const rec = await uploadFile(file)
          const stored = {
            ...pending,
            status: 'ready',
            file_id: rec.file_id || rec.id,
            name: rec.name || pending.name,
            size: rec.size ?? pending.size,
            mime_type: rec.mime_type || pending.mime_type,
            workspace_path: rec.path || rec.workspace_path || null,
          }
          results.push(stored)
          setItems((prev) =>
            prev.map((it) => (it.key === key ? stored : it)),
          )
        } catch (err) {
          const message = err?.message || 'Upload failed'
          setItems((prev) =>
            prev.map((it) =>
              it.key === key ? { ...it, status: 'error', error: message } : it,
            ),
          )
          fail(`${file.name}: ${message}`)
        }
      }
      return results
    },
    [fail, limits],
  )

  const remove = useCallback((key) => {
    setItems((prev) => {
      const target = prev.find((it) => it.key === key)
      if (target?.previewUrl) {
        try {
          URL.revokeObjectURL(target.previewUrl)
        } catch {
          /* already revoked */
        }
      }
      return prev.filter((it) => it.key !== key)
    })
  }, [])

  const clear = useCallback(() => {
    setItems((prev) => {
      prev.forEach((it) => {
        if (it.previewUrl) {
          try {
            URL.revokeObjectURL(it.previewUrl)
          } catch {
            /* already revoked */
          }
        }
      })
      return []
    })
  }, [])

  // Retry a file that failed to upload.
  const retry = useCallback(
    async (key) => {
      const target = itemsRef.current.find((it) => it.key === key)
      if (!target) return
      try {
        const rec = await uploadFile(target.file)
        const stored = {
          ...target,
          status: 'ready',
          file_id: rec.file_id || rec.id,
          workspace_path: rec.path || rec.workspace_path || null,
          error: null,
        }
        setItems((prev) => prev.map((it) => (it.key === key ? stored : it)))
      } catch (err) {
        const message = err?.message || 'Upload failed'
        setItems((prev) =>
          prev.map((it) =>
            it.key === key ? { ...it, status: 'error', error: message } : it,
          ),
        )
        fail(`${target.name}: ${message}`)
      }
    },
    [fail],
  )

  const ready = items.filter((it) => it.status === 'ready' && it.file_id)
  const uploading = items.some((it) => it.status === 'uploading')

  return {
    items,
    ready,
    uploading,
    addFiles,
    remove,
    clear,
    retry,
    count: items.length,
  }
}
