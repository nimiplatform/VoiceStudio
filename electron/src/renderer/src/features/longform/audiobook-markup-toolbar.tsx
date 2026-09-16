import type { RefObject } from 'react';
import {
  AudioLinesIcon,
  BoldIcon,
  ChevronsLeftIcon,
  ChevronsRightIcon,
  PauseIcon,
  SpellCheckIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';

export function AudiobookMarkupToolbar({
  textareaRef,
  text,
  disabled,
  setText,
}: {
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  text: string;
  disabled: boolean;
  setText(value: string): void;
}) {
  const { t } = useTranslation();
  const focus = (start: number, end = start) =>
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(start, end);
    });
  const splice = (snippet: string, start: number, end: number) => {
    const input = textareaRef.current;
    if (!input) return;
    input.setRangeText(snippet, start, end, 'end');
    setText(input.value);
  };
  const insert = (snippet: string) => {
    const input = textareaRef.current;
    if (!input) return;
    const start = input.selectionStart ?? text.length;
    const end = input.selectionEnd ?? start;
    splice(snippet, start, end);
    focus(start + snippet.length);
  };
  const wrap = (open: string, close: string) => {
    const input = textareaRef.current;
    if (!input) return;
    const start = input.selectionStart ?? text.length;
    const end = input.selectionEnd ?? start;
    const selected = input.value.slice(start, end);
    splice(open + selected + close, start, end);
    focus(selected ? start + open.length + selected.length + close.length : start + open.length);
  };
  const insertVoice = () => {
    const input = textareaRef.current;
    if (!input) return;
    const start = input.selectionStart ?? text.length;
    const end = input.selectionEnd ?? start;
    splice('[voice:NAME]', start, end);
    focus(start + 7, start + 11);
  };

  return (
    <div
      role="toolbar"
      aria-label={t('audiobook.markup_toolbar')}
      className="flex flex-wrap items-center gap-1 rounded-xl border border-border/50 bg-muted/15 p-1.5"
    >
      <Button size="xs" variant="ghost" disabled={disabled} onClick={() => insert('[pause 500ms]')}>
        <PauseIcon />
        {t('audiobook.insert_pause')}
      </Button>
      <Button size="xs" variant="ghost" disabled={disabled} onClick={insertVoice}>
        <AudioLinesIcon />
        {t('audiobook.insert_voice')}
      </Button>
      <Button
        size="xs"
        variant="ghost"
        disabled={disabled}
        onClick={() => wrap('[slow]', '[/slow]')}
      >
        <ChevronsLeftIcon />
        {t('audiobook.insert_slow')}
      </Button>
      <Button
        size="xs"
        variant="ghost"
        disabled={disabled}
        onClick={() => wrap('[fast]', '[/fast]')}
      >
        <ChevronsRightIcon />
        {t('audiobook.insert_fast')}
      </Button>
      <Button
        size="xs"
        variant="ghost"
        disabled={disabled}
        onClick={() => wrap('[emphasis]', '[/emphasis]')}
      >
        <BoldIcon />
        {t('audiobook.insert_emphasis')}
      </Button>
      <Button
        size="xs"
        variant="ghost"
        disabled={disabled}
        onClick={() => wrap('[spell]', '[/spell]')}
      >
        <SpellCheckIcon />
        {t('audiobook.insert_spell')}
      </Button>
      <p className="w-full px-2 text-xs text-muted-foreground">{t('nimi.referenceControls')}</p>
    </div>
  );
}
