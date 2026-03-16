import { defineConfig } from 'astro/config';
import svelte from '@astrojs/svelte';

export default defineConfig({
  integrations: [svelte()],
  output: 'static',
  site: 'https://shahriarspace.github.io',
  base: '/bangla-pdf-translator',
  build: {
    assets: '_assets',
  },
});
