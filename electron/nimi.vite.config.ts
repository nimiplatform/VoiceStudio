import { defineConfig } from 'vite';
import existing from './electron.vite.config';

// Preserve VoiceStudio's actual renderer configuration and shared media worklet.
const config = existing as { renderer: Record<string, unknown> };
export default defineConfig({
  ...config.renderer,
  base: './',
  build: { outDir: '../../../dist', emptyOutDir: true },
});
