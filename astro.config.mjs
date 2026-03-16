import { defineConfig } from 'astro/config';
import svelte from '@astrojs/svelte';

// Astro config — https://docs.astro.build/en/reference/configuration-reference/
export default defineConfig({
  integrations: [svelte()],
  output: 'static',
  site: 'https://shahriarspace.github.io',
  base: '/bangla-pdf-translator/',
  build: {
    assets: '_assets',
  },
});
