import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";

export default {
  preprocess: vitePreprocess(),
  compilerOptions: {
    // Enable Svelte 4 legacy component API (new Component({ target }), $:, export let, etc.)
    compatibility: {
      componentApi: 4,
    },
  },
};
