import { WrenchIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

export function AgentFixButton({ request: _request, className }: { request: string; className?: string }) {
  const { t } = useTranslation();
  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      className={cn('shrink-0', className)}
      onClick={() => { window.location.hash = '/settings/models'; }}
    >
      <WrenchIcon />
      {t('nimi.configuration')}
    </Button>
  );
}
