import { defineConfig } from 'astro/config';
import svelte from '@astrojs/svelte';

export default defineConfig({
  integrations: [svelte()],
  output: 'static',
  site: 'https://minemeraj.github.io',
  base: '/bangla-pdf-to-eng-book',
  build: {
    assets: '_assets',
  },
});
