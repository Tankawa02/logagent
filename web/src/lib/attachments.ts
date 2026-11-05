import type { AttachmentMeta } from './types'

/** 和服务端 web/attachments.py 的上限保持一致；服务端仍会再校验一遍 */
export const MAX_ATTACHMENTS = 6
const IMAGE_MAX_BYTES = 5 * 1024 * 1024
const TEXT_MAX_BYTES = 2 * 1024 * 1024
/** 长边超过这个尺寸的图片先缩小：模型看截图用不到更高的分辨率，还能省下不少 tokens */
const IMAGE_MAX_EDGE = 2048
/** 不需要缩小、又不算大的图片原样上传，保留截图里的文字清晰度 */
const IMAGE_KEEP_BYTES = 1.5 * 1024 * 1024

const IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif'])
const TEXT_EXTENSIONS = [
  'log',
  'txt',
  'out',
  'err',
  'trace',
  'json',
  'jsonl',
  'ndjson',
  'yaml',
  'yml',
  'xml',
  'csv',
  'tsv',
  'md',
  'ini',
  'conf',
  'cfg',
  'properties',
  'toml',
  'env',
  'sql',
  'sh',
  'py',
  'java',
  'kt',
  'go',
  'js',
  'ts',
  'tsx',
  'jsx',
  'rb',
  'php',
  'cs',
  'c',
  'h',
  'cc',
  'cpp',
  'rs',
  'scala',
  'swift',
  'gradle',
  'diff',
  'patch',
  'html',
]

export const ACCEPT = [...IMAGE_TYPES, ...TEXT_EXTENSIONS.map((ext) => `.${ext}`), 'text/*'].join(',')

export const DEFAULT_ATTACHMENT_QUESTION = '请结合我上传的附件分析。'

export interface DisplayAttachment extends AttachmentMeta {
  /** 图片的 data URL（页面 CSP 只允许 data: 图片）；历史轮次没有存原图，只显示文件名 */
  preview?: string
}

export interface PendingAttachment extends DisplayAttachment {
  id: string
  mime: string
  /** base64，不带 data: 前缀 */
  data: string
}

export class AttachmentReadError extends Error {}

function extension(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot === -1 ? '' : name.slice(dot + 1).toLowerCase()
}

function isTextFile(file: File): boolean {
  return file.type.startsWith('text/') || file.type === 'application/json' || TEXT_EXTENSIONS.includes(extension(file.name))
}

function readAsDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error ?? new Error('读取失败'))
    reader.readAsDataURL(blob)
  })
}

function canvasBlob(canvas: HTMLCanvasElement, type: string, quality?: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('图片转换失败'))), type, quality)
  })
}

async function shrinkImage(file: File): Promise<Blob> {
  // 动图缩放会丢帧：GIF 原样上传
  if (file.type === 'image/gif') return file
  const bitmap = await createImageBitmap(file)
  try {
    const scale = Math.min(1, IMAGE_MAX_EDGE / Math.max(bitmap.width, bitmap.height))
    if (scale === 1 && file.size <= IMAGE_KEEP_BYTES) return file
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(bitmap.width * scale))
    canvas.height = Math.max(1, Math.round(bitmap.height * scale))
    const context = canvas.getContext('2d')
    if (!context) return file
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
    // 截图优先保持 PNG（文字更清晰），还是太大时退到高质量 JPEG
    if (file.type === 'image/png') {
      const png = await canvasBlob(canvas, 'image/png')
      if (png.size <= IMAGE_KEEP_BYTES * 2) return png
    }
    return await canvasBlob(canvas, 'image/jpeg', 0.9)
  } finally {
    bitmap.close()
  }
}

function pastedName(file: File, index: number): string {
  // 系统截图粘贴进来通常都叫 image.png，换成带时间的名字，方便在对话里区分
  if (file.name && file.name !== 'image.png') return file.name
  const now = new Date()
  const stamp = [now.getHours(), now.getMinutes(), now.getSeconds()].map((n) => String(n).padStart(2, '0')).join('')
  return `截图-${stamp}${index ? `-${index + 1}` : ''}.png`
}

export async function readAttachment(file: File, index = 0): Promise<PendingAttachment> {
  const id = `att-${crypto.randomUUID()}`
  const name = pastedName(file, index)
  if (IMAGE_TYPES.has(file.type)) {
    let blob: Blob
    try {
      blob = await shrinkImage(file)
    } catch {
      throw new AttachmentReadError(`「${name}」无法作为图片读取`)
    }
    if (blob.size > IMAGE_MAX_BYTES) throw new AttachmentReadError(`图片「${name}」压缩后仍超过 5 MB`)
    const dataUrl = await readAsDataUrl(blob)
    return {
      id,
      name,
      kind: 'image',
      mime: blob.type || file.type,
      size: blob.size,
      data: dataUrl.split(',', 2)[1] ?? '',
      preview: dataUrl,
    }
  }
  if (file.type.startsWith('image/')) {
    throw new AttachmentReadError(`「${name}」的图片格式不支持，请转成 PNG / JPEG / WebP / GIF`)
  }
  if (!isTextFile(file)) {
    throw new AttachmentReadError(`「${name}」不是支持的文件类型：目前支持图片和文本文件（日志、配置、堆栈、源码等）`)
  }
  if (file.size === 0) throw new AttachmentReadError(`「${name}」是空文件`)
  if (file.size > TEXT_MAX_BYTES) {
    throw new AttachmentReadError(`「${name}」超过 2 MB：大文件请在「来源」里登记为会话日志，agent 能按需检索全文`)
  }
  const head = new Uint8Array(await file.slice(0, 8192).arrayBuffer())
  if (head.includes(0)) throw new AttachmentReadError(`「${name}」看起来是二进制文件，目前只支持图片和文本文件`)
  // 原始字节交给服务端：编码（UTF-8 / GBK 等）由服务端和读日志时一样自动识别
  const dataUrl = await readAsDataUrl(file)
  return { id, name, kind: 'text', mime: file.type || 'text/plain', size: file.size, data: dataUrl.split(',', 2)[1] ?? '' }
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function toDisplay({ name, kind, size, truncated, preview }: PendingAttachment): DisplayAttachment {
  return { name, kind, size, truncated, preview }
}

export function toUpload({ name, mime, data }: PendingAttachment) {
  return { name, mime, data }
}
