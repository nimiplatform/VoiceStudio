import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { NimiAIConfigSnapshot } from '@nimiplatform/sdk/ai';
import { ModelConfigAIConfigSurface } from '@nimiplatform/kit/features/model-config';
import { getNimiClient, NIMI_APP_ID, NIMI_CAPABILITIES } from '@/lib/nimi-client';

// Aligns the generated reference's AIConfig panel with VoiceStudio's Settings page.
export function NimiSettings() {
  const { t } = useTranslation();
  const [snapshot, setSnapshot] = useState<NimiAIConfigSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSnapshot(await getNimiClient().aiConfig.get());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  return <ModelConfigAIConfigSurface
    context={{ owner: 'app-ai-config', appId: NIMI_APP_ID }}
    capabilityContracts={NIMI_CAPABILITIES}
    capabilities={snapshot?.config ? snapshot.config.capabilities as never : snapshot ? null : undefined}
    revision={snapshot?.revision}
    effectiveSelections={snapshot?.effectiveSelections}
    listOptions={(query) => getNimiClient().aiConfig.listOptions(query)}
    loading={loading && !snapshot}
    loadError={error}
    onRetry={() => { void refresh(); }}
    onOverwrite={async (input) => {
      const result = await getNimiClient().aiConfig.overwrite(input);
      if (result.outcome === 'committed') await refresh();
      return result;
    }}
    copy={{ title: t('nimi.configuration'), description: t('nimi.configuration_description') }}
  />;
}
