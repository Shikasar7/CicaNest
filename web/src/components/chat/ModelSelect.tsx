import { NativeSelect } from '@/components/ui/native-select'
import type { ProviderView } from '@/lib/api'

/** Which model she talks with. */
export function ModelSelect({ id, value, onChange, providers, optional = false }: {
  id: string
  value: string
  onChange: (v: string) => void
  providers: ProviderView[]
  optional?: boolean
}) {
  return (
    <NativeSelect id={id} value={value} onChange={(e) => onChange(e.target.value)} className="w-full">
      {optional && <option value="">先不选（她不会说话）</option>}
      {providers.map((p) => (
        <option key={p.id} value={p.id}>
          {p.id}（{p.model}）
        </option>
      ))}
    </NativeSelect>
  )
}
