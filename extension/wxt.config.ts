import { defineConfig } from 'wxt';

// WXT config: https://wxt.dev/api/config.html
export default defineConfig({
  modules: ['@wxt-dev/module-react'],
  srcDir: '.',
  outDir: '.output',
  manifest: {
    name: 'PaperPrism',
    description:
      'Auto-organize arxiv papers into a local, multi-dimensional library.',
    version: '0.2.1',
    permissions: [
      'downloads',
      'storage',
      'notifications',
    ],
    host_permissions: [
      // Needed for chrome.downloads.download(url) on manual archive.
      'https://arxiv.org/*',
      // Needed for fetch() against the local Agent.
      'http://127.0.0.1/*',
      'http://localhost/*',
    ],
    action: {
      default_title: 'PaperPrism',
      default_popup: 'popup.html',
    },
    options_ui: {
      page: 'options.html',
      open_in_tab: true,
    },
    icons: {
      '16': 'icon/16.png',
      '32': 'icon/32.png',
      '48': 'icon/48.png',
      '128': 'icon/128.png',
    },
  },
});
