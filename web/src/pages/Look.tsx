import { AppearanceSettings } from '@/components/Appearance'

/** 外观: light or dark, the theme, an accent colour, sharing themes. */
export function Look() {
  return (
    <section className="rounded-2xl border bg-card p-5">
      <AppearanceSettings />
    </section>
  )
}
