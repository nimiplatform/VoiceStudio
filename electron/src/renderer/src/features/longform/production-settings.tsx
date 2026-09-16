import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Switch } from '@/components/ui/switch';
import { DEFAULT_OVERRIDES, type Overrides } from '../../../../../../frontend/src/utils/longformOverrides';
export function ProductionSettings({value,disabled,onChange}: {value:Overrides;disabled:boolean;onChange:(value:Overrides)=>void}) {
  const {t}=useTranslation();
  return <details className="space-y-4">
    <summary className="cursor-pointer text-sm font-medium">{t('audiobook.expressive')}</summary>
    <label className="flex items-center justify-between gap-2 text-xs">{t('clone.postprocess')}
      <Switch aria-label={t('clone.postprocess')} disabled={disabled} checked={value.postprocess??true} onCheckedChange={(postprocess)=>onChange({...value,postprocess})}/>
    </label>
    <label className="flex items-center justify-between gap-2 text-xs" title={t('audiobook.vary_repeats_help')}>{t('audiobook.vary_repeats')}
      <Switch aria-label={t('audiobook.vary_repeats')} disabled={disabled} checked={value.varyRepeats} onCheckedChange={(varyRepeats)=>onChange({...value,varyRepeats})}/>
    </label>
    <Button variant="ghost" disabled={disabled} onClick={()=>onChange({...DEFAULT_OVERRIDES})}>{t('audiobook.reset')}</Button>
  </details>;
}
