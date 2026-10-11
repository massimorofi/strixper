# AI-Chat Enhancement Specification: Copilot-Style Features

> **Revision 2 — validated against the codebase and the live Halogen engine.**
> Changes from the original draft are marked **[corrected]**, **[confirmed]** or
> **[new]**. The corrections below are not stylistic: each one was checked
> against `backend/app/`, the running engine's `/health`, and the OpenAI
> Agents SDK converter before being written down.

## Overview

Transform the existing AI-Chat tab into a **Microsoft Copilot-style**
conversational interface with:

- **Image analysis** (upload images for vision models)
- **File upload/download** (attach files for analysis, download generated artifacts)
- **Persistent workspace** for file operations
- **Multi-modal messaging** (text + images + files in single turns)

---

## Validation findings

Six claims in the original draft did not survive contact with the code. They
are corrected throughout this document.

1. **[corrected] Image dimension limit.** The draft specified "max
   4096x4096". The live engine reports otherwise:

   ```json
   "vision": {
     "enabled": true,
     "endpoints": ["/v1/chat/completions", "/v1/messages", "/v1/responses"],
     "input": "data: URL or bare base64 in an image content part; http(s) URLs are refused",
     "max_pixels": 3686400,
     "size_multiple": 32
   }
   ```

   `max_pixels: 3686400` is ~1920x1920, not 4096x4096, and both sides must
   be multiples of 32. Validation is therefore **pixel-count based**, read
   from the engine's own `/health` rather than hard-coded, so a different
   engine build gets its own limit.

2. **[corrected] `python-multipart` is not installed.** FastAPI needs it for
   any `multipart/form-data` route. It was absent from
   `backend/requirements.txt`. Added.

3. **[corrected] `content: str = Field(..., min_length=1)` blocks
   image-only messages.** Both `ChatMessage` (`chat.py`) and
   `AgentChatMessage` (`agent.py`) reject empty content, and a second
   validator rejects whitespace-only content. An attachment-only turn --
   drag a picture in with no text, the common Copilot gesture -- fails
   validation before it reaches any handler. The floor is relaxed to
   `min_length=0` with a model-level check that *something* is present.

4. **[corrected] Workspace root.** The draft proposed
   `$HOME/.strixper/temp/`. The codebase already has a workspace concept:
   `settings.agent_workspace` (`AGENT_WORKSPACE`, default
   `backend/agent_workspace`), documented in README and `doc/strixper_specs.md`,
   and mounted as the named Docker volume `strixper-agent-ws` in
   `strixper.sh`. Introducing a second root would split agent-visible files
   from user-visible files and break the existing volume mount. **The
   workspace is `AGENT_WORKSPACE`.** Uploads and outputs are subdirectories
   of it, so the agent sees exactly what the user uploads and vice versa.

5. **[corrected] `workspace_list` / `workspace_read` / `workspace_write`
   already exist.** `backend/app/services/agent_exec_tools.py` ships
   `list_directory`, `read_file` and `write_file` bound to the workspace,
   with the `AGENT_MAX_WRITE_CHARS` chunking guard. Adding `workspace_*`
   aliases would double the tool count and confuse tool selection for no
   gain. Only **`delete_path`** is genuinely missing, and it is the one tool
   this spec adds.

6. **[confirmed] Halogen refuses `http(s)` image URLs.** Verified live.
   Only `data:` URLs or bare base64 are accepted. The draft's fallback note
   was right; the implementation therefore never forwards a remote URL and
   always inlines bytes it holds.

### Verified live before implementation

| Check | Result |
|-------|--------|
| Vision via `image_url` on `/v1/chat/completions` | Pass: answered "Red" for a red test image |
| Vision via `image`/`base64` source on `/v1/messages` | Pass |
| Vision via `input_image` on `/v1/responses` | Pass |
| Vision **combined with tool calling** | Pass: engine issued `tool_calls` with an image present |
| Engine version | API/engine 0.18.0, match `true` |
| Existing backend test suite | 44 passed (baseline before changes) |

The last row of that table matters most: vision + tools working together is
what makes the agentic path viable rather than a separate vision-only mode.

---

## Architecture

### Current Stack

| Layer | Technology |
|-------|------------|
| Frontend | React 18, TanStack Query, SSE streaming |
| Backend | FastAPI, OpenAI Agents SDK, Halogen engine proxy |
| Engine | Halogen (OpenAI-compatible, vision-capable) |
| Storage | LocalStorage (conversation), Docker volumes (backend data) |

### New and modified components

```
frontend/src/
|-- components/
|   |-- AIChatTab.jsx          # MODIFIED: composer attachments, image grid
|   |-- FileUploadZone.jsx     # NEW: drag-drop + picker, validation
|   |-- MessageAttachments.jsx # NEW: thumbnails + file chips in bubbles
|   `-- WorkspaceFileTree.jsx  # NEW: browse the workspace
|-- hooks/
|   `-- useFileUpload.js       # NEW: upload state machine
|-- api.js                     # MODIFIED: file/workspace endpoints
`-- utils/
    `-- fileUtils.js           # NEW: MIME detection, size limits, formatting

backend/app/
|-- routers/
|   |-- chat.py                # MODIFIED: attachments -> vision content parts
|   |-- files.py               # NEW: upload / download / workspace API
|   `-- agent.py               # MODIFIED: attachments -> SDK input parts
|-- services/
|   |-- file_store.py          # NEW: workspace file management + TTL sweep
|   `-- vision_helper.py       # NEW: image sniffing, dimensions, data URLs
`-- config.py                  # MODIFIED: upload/vision settings
```

No image *generation* -- Halogen does not support it.

---

## Feature 1: Image Upload & Analysis

### Upload model

Images are **uploaded once, referenced by id thereafter**. The client does
not paste base64 into every message of the history; it uploads the file,
gets a `file_id`, and the backend expands that id into a `data:` URL at
the moment the request goes upstream. This keeps the client->backend payload
small on every turn after the first, and it means the bytes are on disk
where the agent can reach them too.

### Frontend

**FileUploadZone.jsx**
- Drag-drop over the composer, plus a paperclip button for the picker
- Accept: `image/png`, `image/jpeg`, `image/webp`, `image/gif`
- Max size: `MAX_IMAGE_SIZE_MB` (default 10 MB) per image
- Up to `MAX_IMAGES_PER_MESSAGE` (default 5) per turn
- Thumbnail per pending image, individually removable, upload progress
- Client-side rejection with a specific message per rejection reason

**Message model extension**

```javascript
{
  id: "m123",
  role: "user",
  content: "Analyze this chart",
  attachments: [
    {
      type: "image",
      file_id: "a1b2c3d4...",
      name: "chart.png",
      size: 123456,
      mime_type: "image/png",
      workspace_path: "uploads/<chat>/chart.png"
    }
  ],
  streaming: false
}
```

`workspace_path` is relative to the workspace root and is what the agent
is told to open.

### Backend

**`vision_helper.py`** -- pure stdlib, no Pillow.

- `sniff_mime(bytes) -> str` -- magic bytes, not the client-supplied
  `Content-Type`. A browser will happily label a `.html` file
  `image/png`; the client header is treated as a hint and the bytes as
  truth.
- `read_image_size(bytes, mime) -> (width, height)` -- header parse for
  PNG (IHDR), JPEG (SOF0/1/2 markers), GIF (logical screen descriptor),
  WebP (VP8/VP8L/VP8X chunks).
- `validate_image(bytes, max_pixels, size_multiple)` -- checks the
  engine's real constraints, read from `/health`.
- `to_data_url(bytes, mime)` -- `data:<mime>;base64,<...>`.

**`chat.py`** -- `_build_payload()` for `api="chat"`:

```python
{"role": "user", "content": [
    {"type": "text", "text": "Analyze this"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
]}
```

Text-only turns keep sending a plain string. The array form is used only
when a message carries attachments, so nothing about the existing
text path changes on the wire.

`api="messages"` gets the Anthropic shape instead:

```python
{"role": "user", "content": [
    {"type": "image", "source": {"type": "base64",
                                 "media_type": "image/png", "data": "..."}},
    {"type": "text", "text": "Analyze this"}
]}
```

`api="responses"` gets `input_image` parts in the `input` array.

**`api="completions"` cannot carry images** -- it is raw text completion and
has no content-part concept. A request that combines attachments with
`api="completions"` is rejected with 422 and an explicit message rather
than silently dropping the images.

### Vision capability gate

Before sending images upstream, the backend checks the engine's
`/health -> vision.enabled`. When the loaded engine has no vision tower,
the request fails fast with a clear, actionable error rather than a
downstream 400 three layers away. The check is cached per engine target and
refreshed with the normal poll, so this costs one field read per turn.

---

## Feature 2: File Upload & Download

### Frontend

**FileUploadZone.jsx** (extended)
- Non-image files allowed (`allowed_file_types`, default `["*"]`)
- Max size: `MAX_FILE_SIZE_MB` (default 50 MB)
- File chip: icon by type, name, size, remove button
- Progress bar during upload

**MessageAttachments.jsx**
- Rendered in user and assistant bubbles
- Images: thumbnail grid, click for full-size overlay
- Other files: chip with icon, name, size, download button
- Assistant-generated files carry a sparkle badge

### Backend -- `files.py`

```
POST   /api/v1/files/upload            multipart: file, optional subdir
  -> { file_id, name, size, mime_type, workspace_path, download_url, preview_url }

GET    /api/v1/files                  -> { files: [{id,name,size,mtime,path,is_dir}] }
GET    /api/v1/files/{file_id}        -> FileResponse (attachment)
GET    /api/v1/files/{file_id}/preview-> FileResponse (inline)
DELETE /api/v1/files/{file_id}        -> { deleted: true }

GET    /api/v1/workspace/list?path=.  -> { path, entries: [...] }
GET    /api/v1/workspace/read?path=   -> { content, encoding, truncated }
DELETE /api/v1/workspace?path=        -> { deleted: true }
```

Upload and workspace listing are separate concerns and separate routes:
upload is what the composer calls, the workspace routes are what the file
browser calls.

### Workspace layout

Root: `AGENT_WORKSPACE` (default `backend/agent_workspace`, Docker volume
`strixper-agent-ws`).

```
<workspace>/
|-- .strixper-index.json    # file_id -> metadata (owned by file_store)
|-- uploads/<chat_id>/      # user uploads
`-- outputs/<chat_id>/      # agent-generated artifacts
```

The index is a JSON file inside the workspace so it travels with the same
volume. It is written atomically (temp file + `os.replace`) and reloaded on
startup; a corrupt index is rebuilt by scanning the tree rather than
crashing the backend.

---

## Feature 3: Workspace Integration for Agent

The full-access tier already has `read_file`, `write_file`,
`list_directory`. This spec adds the missing destructive counterpart:

```python
@function_tool
def delete_path(path: str) -> str:
    """Delete a file, or an empty directory, inside the workspace."""
```

`delete_path` refuses non-empty directories. Recursive deletion is a
`run_shell` away for an agent that genuinely needs it; giving the tool a
`rm -rf` equivalent by default is a larger footgun than the convenience
is worth.

**Uploaded files are announced to the model.** When a turn carries
attachments, the instructions gain a short block naming each file and its
workspace-relative path. Without this the model does not know the files
exist and has to be told by hand every time.

### Security

- **Path traversal**: every path is resolved with `os.path.realpath` and
  checked against the resolved workspace root with
  `os.path.commonpath`. Symlinks are resolved before the check, so a
  planted symlink out of the tree is caught rather than trusted.
- **Filename sanitisation**: the client-supplied filename is reduced to a
  basename, control characters and path separators stripped, and the
  stored name is never used to build a path without the workspace prefix.
- **Read limits**: `workspace_read` caps at 1 MB of text and reports
  `truncated` rather than failing.
- **Write limits**: unchanged -- `AGENT_MAX_WRITE_CHARS` per `write_file`
  call, with the chunked-append guidance.
- **Binary files**: upload/download only. `workspace_read` detects
  non-UTF-8 content and refuses with an explanation.
- **No authentication is added by this feature.** The dashboard has no
  auth layer today; these routes are reachable by whoever can reach the
  port, exactly like `run_shell`. That is a pre-existing property of the
  deployment model, not something this change introduces or remedies, and
  it should be read as a reason to keep the dashboard off untrusted
  networks.

---

## API Extensions

### Chat request

```python
class Attachment(BaseModel):
    type: Literal["image", "file"]
    file_id: str
    name: str
    size: int
    mime_type: str
    workspace_path: Optional[str] = None

class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str = Field(default="", max_length=MAX_MESSAGE_CHARS)
    attachments: list[Attachment] = Field(default_factory=list)

    # model_validator: content.strip() OR attachments must be non-empty
```

The `min_length=1` is gone; the model-level validator replaces it with
the rule that actually matters -- a message must contain *something*.

### File upload response

```python
class FileUploadResponse(BaseModel):
    file_id: str
    name: str
    size: int
    mime_type: str
    workspace_path: str
    download_url: str
    preview_url: Optional[str] = None
```

---

## Configuration

`backend/app/config.py` additions. Existing keys are untouched.

```python
# Upload limits
self.max_image_size_mb: int = _get_int("MAX_IMAGE_SIZE_MB", 10)
self.max_file_size_mb: int = _get_int("MAX_FILE_SIZE_MB", 50)
self.max_images_per_message: int = _get_int("MAX_IMAGES_PER_MESSAGE", 5)

# Workspace retention: files older than this are swept at startup
self.workspace_ttl_days: int = _get_int("WORKSPACE_TTL_DAYS", 7)

# Allowed MIME types (comma-separated env values)
self.allowed_image_types: list[str] = _get_list(
    "ALLOWED_IMAGE_TYPES",
    ["image/png", "image/jpeg", "image/webp", "image/gif"],
)
self.allowed_file_types: list[str] = _get_list("ALLOWED_FILE_TYPES", ["*"])
```

`_get_list` follows the existing `_get_int` / `_get_float` pattern:
missing or malformed falls back to the default rather than raising.

**Note on `MAX_FILE_SIZE_MB` vs proxy limits.** If the dashboard sits
behind nginx or another reverse proxy, the proxy's `client_max_body_size`
must be at least as large or the upload is killed before FastAPI sees it,
producing a confusing 413 with no application error body. Documented in
the README deployment section.

### Frontend (Vite proxy)

The existing `/api` proxy already covers every new route, since all of
them live under `/api/v1/...`. **No `vite.config.js` change is needed** --
the draft's `/files` proxy entry assumed routes outside `/api`.

---

## User Experience Flow

### 1. Image analysis

```
User drags chart.png -> FileUploadZone uploads immediately -> thumbnail
User types "What does this show?" + Enter
-> message carries attachment{file_id}
-> backend reads bytes, builds data: URL, checks vision.enabled
-> Halogen vision, streamed answer
```

### 2. File upload for analysis

```
User drags data.csv -> chip appears
User types "Summarize this data" + Enter
-> saved to <workspace>/uploads/<chat_id>/data.csv
-> instructions tell the model the file is there
-> agent calls read_file("uploads/<chat_id>/data.csv")
-> agent analyses, responds
```

### 3. File generation & download

```
User: "Create a Python script to plot this data"
-> agent write_file("outputs/<chat_id>/plot.py")
-> frontend renders sparkle plot.py chip
-> click -> GET /api/v1/files/{id} -> download
```

### 4. Workspace browser

```
WorkspaceFileTree.jsx browses the workspace:
uploads/  outputs/
Click -> preview (text/image) or download
```

---

## Implementation Priority

| Phase | Features | Status |
|-------|----------|--------|
| **1** | `file_store`, `vision_helper`, `files.py` router, settings, `python-multipart` | Done |
| **2** | Image attachments in `chat.py` (all 4 API styles) + vision gate | Done |
| **3** | Attachments in `agent.py`, `delete_path` tool, instruction injection | Done |
| **4** | Frontend: `fileUtils`, `useFileUpload`, `FileUploadZone`, `MessageAttachments`, `AIChatTab` integration | Done |
| **5** | `WorkspaceFileTree` browser | Done |
| **6** | Backend + frontend tests, live end-to-end | Done |
| -- | Image generation | Not supported by Halogen |

---

## Testing Checklist

Backend:
- [x] Single image upload -> vision analysis works
- [x] Multiple images in one message
- [x] File upload (CSV, TXT) -> agent can read via tool
- [x] Assistant generates file -> appears -> download works
- [x] Path traversal blocked (`..`, absolute, symlink)
- [x] Oversized file rejected with clear error
- [x] Wrong-MIME file rejected (bytes != claimed type)
- [x] Non-vision engine -> clear error, not a downstream 400
- [x] `api="completions"` + images -> rejected
- [x] Image-only message (empty text) accepted
- [x] Corrupt index rebuilt on startup
- [x] TTL sweep removes expired files, keeps fresh ones
- [x] Existing 44 tests still pass

Frontend:
- [x] `npm run build` clean
- [x] Upload -> thumbnail -> send -> streamed reply

---

## Notes

- **No image generation** -- Halogen does not support it.
- **Workspace is per-deployment**, not per-user. In a multi-user
  deployment, namespace by user id under the workspace root; nothing in
  the current product has a user concept to namespace with.
- **Cleanup** runs at startup (TTL sweep). A long-lived process will not
  sweep mid-run; a cron or a periodic task is a follow-up if the
  deployment never restarts.
- **Streaming + multipart are decoupled.** Upload is its own request that
  completes before the chat request starts. The draft's "frontend sends
  multipart, backend extracts images, forwards JSON" would have coupled
  the two and made the SSE stream depend on upload handling. The chat
  request stays JSON.
