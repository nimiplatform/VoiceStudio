import type { ReactElement } from 'react';

export interface VoiceSelectorProps {
  value?: string;
  onChange: (value: string) => void;
  profiles?: Array<{ id: string; name: string; instruct?: string; kind?: string }>;
  presets?: boolean;
  gallery?: boolean;
  speakerClones?: Record<string, unknown> | null;
  engineDefault?: boolean;
  defaultLabel?: string;
  onPreview?: (value: string) => void;
  previewLoading?: boolean;
  onJumpToGallery?: () => void;
  onCreateVoice?: () => void;
  recentsKey?: string;
  placeholder?: string;
  ariaLabel: string;
  disabled?: boolean;
  size?: string;
  buttonClassName?: string;
  menuPortal?: boolean;
}

export default function VoiceSelector(props: VoiceSelectorProps): ReactElement;
