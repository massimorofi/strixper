// Syntax highlighting for the workspace preview panel.
//
// Languages are registered explicitly rather than pulling in the whole
// `highlight.js` package: the full bundle is ~1MB of parsed grammars and
// the preview only ever needs a couple of dozen of them. Anything not in
// the registry falls back to `plaintext`, which is what the panel showed
// before highlighting existed, so an unknown extension is never a
// regression -- just uncoloured.

import hljs from 'highlight.js/lib/core'

import bash from 'highlight.js/lib/languages/bash'
import c from 'highlight.js/lib/languages/c'
import cpp from 'highlight.js/lib/languages/cpp'
import css from 'highlight.js/lib/languages/css'
import diff from 'highlight.js/lib/languages/diff'
import dockerfile from 'highlight.js/lib/languages/dockerfile'
import go from 'highlight.js/lib/languages/go'
import graphql from 'highlight.js/lib/languages/graphql'
import ini from 'highlight.js/lib/languages/ini'
import java from 'highlight.js/lib/languages/java'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import kotlin from 'highlight.js/lib/languages/kotlin'
import less from 'highlight.js/lib/languages/less'
import makefile from 'highlight.js/lib/languages/makefile'
import markdown from 'highlight.js/lib/languages/markdown'
import php from 'highlight.js/lib/languages/php'
import plaintext from 'highlight.js/lib/languages/plaintext'
import powershell from 'highlight.js/lib/languages/powershell'
import properties from 'highlight.js/lib/languages/properties'
import python from 'highlight.js/lib/languages/python'
import ruby from 'highlight.js/lib/languages/ruby'
import rust from 'highlight.js/lib/languages/rust'
import scss from 'highlight.js/lib/languages/scss'
import sql from 'highlight.js/lib/languages/sql'
import swift from 'highlight.js/lib/languages/swift'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import yaml from 'highlight.js/lib/languages/yaml'

const LANGS = {
  bash,
  c,
  cpp,
  css,
  diff,
  dockerfile,
  go,
  graphql,
  ini,
  java,
  javascript,
  json,
  kotlin,
  less,
  makefile,
  markdown,
  php,
  plaintext,
  powershell,
  properties,
  python,
  ruby,
  rust,
  scss,
  sql,
  swift,
  typescript,
  xml,
  yaml,
}

for (const [name, def] of Object.entries(LANGS)) {
  hljs.registerLanguage(name, def)
}

// Extension -> hljs language id.
const BY_EXT = {
  bash: 'bash',
  c: 'c',
  cfg: 'ini',
  cjs: 'javascript',
  conf: 'ini',
  cc: 'cpp',
  cpp: 'cpp',
  cxx: 'cpp',
  css: 'css',
  diff: 'diff',
  docker: 'dockerfile',
  gql: 'graphql',
  go: 'go',
  h: 'c',
  hpp: 'cpp',
  htm: 'xml',
  html: 'xml',
  ini: 'ini',
  java: 'java',
  jinja: 'xml',
  js: 'javascript',
  json: 'json',
  jsonc: 'json',
  jsx: 'javascript',
  kt: 'kotlin',
  kts: 'kotlin',
  less: 'less',
  log: 'plaintext',
  mjs: 'javascript',
  md: 'markdown',
  markdown: 'markdown',
  mk: 'makefile',
  patch: 'diff',
  php: 'php',
  ps1: 'powershell',
  psm1: 'powershell',
  py: 'python',
  pyw: 'python',
  rb: 'ruby',
  rs: 'rust',
  sass: 'scss',
  scss: 'scss',
  sh: 'bash',
  sql: 'sql',
  svelte: 'xml',
  swift: 'swift',
  toml: 'ini',
  ts: 'typescript',
  tsx: 'typescript',
  txt: 'plaintext',
  vue: 'xml',
  xml: 'xml',
  yaml: 'yaml',
  yml: 'yaml',
  zsh: 'bash',
}

// Files whose name (not extension) decides the grammar.
const BY_NAME = {
  dockerfile: 'dockerfile',
  makefile: 'makefile',
  'cmakelists.txt': 'cmake' in LANGS ? 'cmake' : 'ini',
  '.gitignore': 'ini',
  '.env': 'ini',
  '.editorconfig': 'ini',
  '.properties': 'properties',
}

function extensionOf(name) {
  const base = (name || '').split('/').pop() || ''
  const dot = base.lastIndexOf('.')
  return dot > 0 ? base.slice(dot + 1).toLowerCase() : ''
}

// Pick a language id for a filename. Never throws; always returns a
// registered id.
export function languageForFile(name) {
  const base = (name || '').split('/').pop() || ''
  const lower = base.toLowerCase()
  if (BY_NAME[lower]) return BY_NAME[lower]
  const ext = extensionOf(name)
  const lang = BY_EXT[ext]
  if (lang && LANGS[lang]) return lang
  return 'plaintext'
}

// Human-readable label for the preview header ("Python", "JSON", ...).
const LABELS = {
  bash: 'Shell',
  c: 'C',
  cpp: 'C++',
  css: 'CSS',
  diff: 'Diff',
  dockerfile: 'Dockerfile',
  go: 'Go',
  graphql: 'GraphQL',
  ini: 'INI',
  java: 'Java',
  javascript: 'JavaScript',
  json: 'JSON',
  kotlin: 'Kotlin',
  less: 'Less',
  makefile: 'Make',
  markdown: 'Markdown',
  php: 'PHP',
  plaintext: 'Plain text',
  powershell: 'PowerShell',
  properties: 'Properties',
  python: 'Python',
  ruby: 'Ruby',
  rust: 'Rust',
  scss: 'SCSS',
  sql: 'SQL',
  swift: 'Swift',
  typescript: 'TypeScript',
  xml: 'HTML/XML',
  yaml: 'YAML',
}

export function languageLabel(lang) {
  return LABELS[lang] || lang
}

// Highlight `code` as `lang`. Returns HTML produced by highlight.js, which
// escapes the source; the only markup in the output is hljs token spans.
export function highlightCode(code, lang) {
  const safe = typeof code === 'string' ? code : String(code ?? '')
  if (!safe) return ''
  try {
    const result = hljs.highlight(safe, { language: lang, ignoreIllegals: true })
    return result.value
  } catch {
    // A grammar blowing up on weird input must not blank the preview.
    return escapeHtml(safe)
  }
}

// Auto-detect fallback for files with no recognised extension.
export function highlightAuto(code) {
  const safe = typeof code === 'string' ? code : String(code ?? '')
  if (!safe) return ''
  try {
    return hljs.highlightAuto(safe).value
  } catch {
    return escapeHtml(safe)
  }
}

function escapeHtml(s) {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

export default hljs
