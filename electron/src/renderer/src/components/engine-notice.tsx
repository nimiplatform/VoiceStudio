import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { useTtsReadiness } from '@/hooks/use-tts-readiness';
export function EngineNotice({ operation = 'tts', requireLocal = false, compact = false }: {
  operation?: string; requireLocal?: boolean; compact?: boolean;
}) {
  const { t } = useTranslation();
  const blocked = useTtsReadiness(operation, requireLocal);
  if (!blocked) return null;
  return <div role="status" className={compact ? 'text-xs' : 'rounded-lg border p-3 text-sm'}>
    <Link to="/settings/models" className="underline">{t('nimi.configuration')}</Link>
    {!compact && <p>{t('nimi.configuration_description')}</p>}
  </div>;
}
