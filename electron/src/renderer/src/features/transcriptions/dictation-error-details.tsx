import { useTranslation } from 'react-i18next';

/** Shared by page dictation and the global widget; never leave an empty error. */
export function DictationErrorDetails({ detail }: { detail?: string }) {
  const { t } = useTranslation();
  return <p role="alert" className="max-h-28 overflow-auto text-sm break-words whitespace-pre-wrap text-destructive">
    {detail?.trim() || t('common.error')}
  </p>;
}
