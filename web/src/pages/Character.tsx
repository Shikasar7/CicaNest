import { useEffect, useState } from 'react'

import { Segmented } from '@/components/Segmented'
import { Personas } from '@/pages/Personas'
import { Tones } from '@/pages/Tones'

type Tab = 'personas' | 'tones'

const tabOf = (): Tab => (window.location.hash === '#/tones' ? 'tones' : 'personas')

/** 人设和语气: who a companion is and how she talks, one page with a tab each. #/personas and #/tones both land
 * here, on their own tab. */
export function Character() {
  const [tab, setTab] = useState<Tab>(tabOf)
  useEffect(() => {
    const update = () => setTab(tabOf())
    window.addEventListener('hashchange', update)
    return () => window.removeEventListener('hashchange', update)
  }, [])
  const choose = (next: Tab) => {
    // The address follows the tab, so a reload or a link keeps it.
    history.replaceState(null, '', next === 'tones' ? '#/tones' : '#/personas')
    setTab(next)
  }
  return (
    <div className="flex flex-col gap-4">
      <Segmented
        label="人设还是语气"
        options={[
          { value: 'personas', label: '人设' },
          { value: 'tones', label: '语气' },
        ]}
        value={tab}
        onChange={choose}
      />
      {tab === 'personas' ? <Personas /> : <Tones />}
    </div>
  )
}
