import { useState } from 'react'

import { FirstLoad, StaleNotice } from '@/components/Feedback'
import { ModelCard } from '@/components/models/ModelCard'
import { ModelForm } from '@/components/models/ModelForm'
import { formFrom, sameForm } from '@/components/models/form'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useDraft, useEditFocus } from '@/lib/drafts'
import { useProviders } from '@/lib/live'
import { liveUsers } from '@/lib/models'

const EMPTY_FORM = formFrom(null)

export function Models() {
  const { data: providers, error, reload } = useProviders()
  const creating = useDraft(EMPTY_FORM, sameForm)
  const { editButton: addButton } = useEditFocus(creating.editing)
  // The model saved last, so its card can offer 测试一下.
  const [savedId, setSavedId] = useState<string | null>(null)

  if (!providers) return <FirstLoad error={error} what="模型" />
  const takenIds = providers.map((p) => p.id)
  const saved = (id: string) => {
    setSavedId(id)
    reload()
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          bot 用这里的模型来想怎么回话。改动保存后立刻生效，不用重启 nest；在「Bot」页给每个 bot 选模型。
        </p>
        {!creating.editing && (
          <Button ref={addButton} onClick={creating.start}>
            添加模型
          </Button>
        )}
      </div>
      <StaleNotice error={error} />
      {creating.editing && (
        <Card>
          <CardHeader>
            <CardTitle>添加模型</CardTitle>
            <CardDescription>填好地址、模型名和密钥；添加后可以先测试一下，再给 bot 用。</CardDescription>
          </CardHeader>
          <CardContent>
            <ModelForm provider={null} takenIds={takenIds} draft={creating} onSaved={(view) => saved(view.id)} />
          </CardContent>
        </Card>
      )}
      {providers.length === 0 && !creating.editing && (
        <Card>
          <CardHeader>
            <CardTitle>还没有模型</CardTitle>
            <CardDescription>点「添加模型」，填写接口地址、模型名和密钥；添加后到「Bot」页给 bot 选用。</CardDescription>
          </CardHeader>
        </Card>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        {providers.map((provider) => (
          <ModelCard
            key={provider.id}
            provider={provider}
            takenIds={takenIds}
            sharerLive={providers.filter((p) => provider.key_shared_with.includes(p.id)).flatMap(liveUsers)}
            justSaved={savedId === provider.id}
            onSaved={(view) => saved(view.id)}
            onDeleted={reload}
          />
        ))}
      </div>
    </div>
  )
}
