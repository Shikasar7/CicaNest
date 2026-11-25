/** Pictures picked for an avatar (a bot's, or yours in the chat window). */

export const PICTURE_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif']
const MAX_FILE = 20 * 1024 * 1024 // before cropping; what is uploaded is a 512 px square

/** A picked picture as a data: URL for the cropper; refuses what cannot be an avatar. */
export function readPicture(file: File): Promise<string> {
  if (!PICTURE_TYPES.includes(file.type)) return Promise.reject(new Error('只能用 PNG、JPEG、WebP 或 GIF 图片'))
  if (file.size > MAX_FILE) return Promise.reject(new Error('图片太大了（超过 20 MB），换一张小一点的'))
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(new Error('读不了这个文件'))
    reader.readAsDataURL(file)
  })
}
