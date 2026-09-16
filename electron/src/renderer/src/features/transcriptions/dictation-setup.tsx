import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
export function DictationSetup({ onReady }: { onReady: () => void }) {
  const { t } = useTranslation();
  return <div className="flex items-center gap-3 rounded-lg border p-3">
    <Link to="/settings/models" className="underline">{t('nimi.configuration')}</Link>
    <Button variant="ghost" onClick={onReady}>{t('common.retry')}</Button>
  </div>;
}
