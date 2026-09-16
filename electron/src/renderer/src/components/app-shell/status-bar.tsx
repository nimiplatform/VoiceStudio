import { Link } from '@tanstack/react-router';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { getNimiClient } from '@/lib/nimi-client';
export function StatusBar({ compact = false }: { compact?: boolean }) {
  const { t } = useTranslation();
  const session = useQuery({ queryKey: ['nimi-session'], queryFn: () => getNimiClient().auth.status(), refetchInterval: 15000 });
  return <div className="border-t p-3 text-xs" role="status">
    <Link to="/settings/models" title={t('nimi.configuration')} className="flex items-center gap-2">
      <span className={'size-2 rounded-full ' + (session.data?.sessionBound ? 'bg-green-500' : 'bg-amber-500')} />
      {compact ? 'Nimi' : t('nimi.configuration')}
    </Link>
    {!compact && !session.data?.sessionBound && <p className="mt-2 break-words">{session.data?.actionHint || session.error?.message}</p>}
  </div>;
}
