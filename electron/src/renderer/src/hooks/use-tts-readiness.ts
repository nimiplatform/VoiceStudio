import { useQuery } from '@tanstack/react-query';
import { getNimiClient } from '@/lib/nimi-client';
import { useBackendStatus } from './use-backend-status';
export type TtsReadinessBlocker = 'engine' | 'loading' | null;
export function useTtsReadiness(operation = 'tts', _requireLocal = false): TtsReadinessBlocker {
  const backend = useBackendStatus();
  const config = useQuery({ queryKey: ['nimi-ai-config'], queryFn: () => getNimiClient().aiConfig.get(), staleTime: 5000, refetchInterval: 15000 });
  if (backend.stage !== 'ready' || config.isLoading) return 'loading';
  const ready = new Set(config.data?.effectiveSelections.filter((item) => item.state === 'ready').map((item) => item.capabilityContract));
  if (!ready.has('audio.synthesize')) return 'engine';
  if (['clone', 'design', 'dub'].includes(operation) && !ready.has('voice.create')) return 'engine';
  return null;
}
