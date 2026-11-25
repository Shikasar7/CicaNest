import { botLook } from './persona.ts'

/** The little face of a bot (components/FaceGlyph) on a canvas, for where there is no DOM: the 3D 小窝. */
export function drawFace(g: CanvasRenderingContext2D, size: number, name: string, id: string): void {
  const look = botLook(name, id)
  const hue = getComputedStyle(document.documentElement).getPropertyValue(look.hue.slice(4, -1)).trim() || '#8a9aa8'
  const over = (paint: string | null, alpha: number, shape: () => void) => {
    shape()
    g.fillStyle = '#fff'
    g.fill()
    if (!paint) return
    g.globalAlpha = alpha
    g.fillStyle = paint
    g.fill()
    g.globalAlpha = 1
  }
  const path = (d: string) => () => {
    const p = new Path2D(d)
    g.fillStyle = '#fff'
    g.fill(p)
    g.globalAlpha = 0.45
    g.fillStyle = hue
    g.fill(p)
    g.globalAlpha = 1
  }
  const oval = (x: number, y: number, rx: number, ry: number, turn = 0) => () => {
    g.beginPath()
    g.ellipse(x, y, rx, ry, (turn * Math.PI) / 180, 0, Math.PI * 2)
  }
  g.save()
  g.scale(size / 48, size / 48)
  if (look.face === 'cat') {
    path('M11 22 L10.5 8.6 Q10.6 6.8 12.2 7.8 L22 14 Z M37 22 L37.5 8.6 Q37.4 6.8 35.8 7.8 L26 14 Z')()
  } else if (look.face === 'bear') {
    for (const x of [13, 35]) {
      over(hue, 0.45, oval(x, 13, 6, 6))
      over(hue, 0.22, oval(x, 13, 3, 3))
    }
  } else if (look.face === 'bunny') {
    for (const [x, turn] of [
      [16.5, -12],
      [31.5, 12],
    ]) {
      over(hue, 0.45, oval(x, 9.5, 4.6, 9, turn))
      over(hue, 0.22, oval(x, 10, 2.1, 6, turn))
    }
  } else {
    over('#86c893', 1, oval(20, 5.6, 4, 2.4, 30))
    over('#86c893', 1, oval(28, 5.6, 4, 2.4, -30))
  }
  path('M24 12 C35 12 41 19 41 28 C41 36.5 34 41 24 41 C14 41 7 36.5 7 28 C7 19 13 12 24 12 Z')()
  for (const x of [17.8, 30.2]) over('#2b3640', 1, oval(x, 26.7, 1.8, 3.7))
  for (const x of [13.6, 34.4]) over('#f7a6ab', 0.85, oval(x, 32.4, 2.9, 1.9))
  g.strokeStyle = '#2b3640'
  g.lineWidth = 1.5
  g.lineCap = 'round'
  g.stroke(new Path2D('M20.6 32.2 Q22.3 34.6 24 32.4 Q25.7 34.6 27.4 32.2'))
  g.restore()
}
