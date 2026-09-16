import type { CSSProperties, ReactElement, ReactNode } from 'react';
export interface SelectOption { value: string; label: string; disabled?: boolean; group?: string; groupLabel?: string }
export interface SearchableSelectProps {
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  placeholder?: string;
  popular?: SelectOption[];
  recentsKey?: string;
  renderLabel?: (option: SelectOption) => ReactNode;
  renderOption?: (option: SelectOption) => ReactNode;
  disabled?: boolean;
  buttonStyle?: CSSProperties;
  buttonClassName?: string;
  ariaLabel: string;
  size?: string;
  renderGroupHeaders?: boolean;
  isRecentable?: (value: string) => boolean;
  onQueryChange?: (value: string) => void;
  onOpenChange?: (open: boolean) => void;
  menuPortal?: boolean;
}
export default function SearchableSelect(props: SearchableSelectProps): ReactElement;
