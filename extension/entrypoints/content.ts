import { defineContentScript } from 'wxt/sandbox';
import { parseArxivIdFromString, pdfUrlFromId } from '@/lib/arxiv';

/**
 * Inject a "Save to PaperPrism" button next to the PDF download link on
 * arxiv abstract pages. Clicking it asks the background script to start
 * a download, which triggers the standard redirect-and-archive flow.
 */
export default defineContentScript({
  matches: ['https://arxiv.org/abs/*', 'https://www.arxiv.org/abs/*'],
  runAt: 'document_idle',
  main() {
    try {
      injectButton();
    } catch (err) {
      console.warn('[PaperPrism] inject failed', err);
    }
  },
});

function injectButton(): void {
  const id = parseArxivIdFromString(location.href);
  if (!id) return;

  // arxiv abs page markup is stable: the right column has a "Download"
  // section with `<ul class="misc"><li>...<a ...>PDF</a>`. We anchor to
  // that list.
  const host =
    document.querySelector<HTMLElement>('.full-text > .abs-button-row') ??
    document.querySelector<HTMLElement>('.full-text > ul') ??
    document.querySelector<HTMLElement>('.extra-services .full-text');
  if (!host) return;

  if (document.getElementById('paperprism-btn')) return;

  const btn = document.createElement('button');
  btn.id = 'paperprism-btn';
  btn.textContent = 'Save to PaperPrism';
  Object.assign(btn.style, {
    marginTop: '8px',
    padding: '6px 12px',
    border: '1px solid #2563eb',
    borderRadius: '4px',
    background: '#2563eb',
    color: 'white',
    fontSize: '13px',
    cursor: 'pointer',
  } as CSSStyleDeclaration);

  btn.addEventListener('click', async (e) => {
    e.preventDefault();
    btn.disabled = true;
    btn.textContent = 'Archiving...';
    try {
      const pdfUrl = pdfUrlFromId(id);
      const res = await chrome.runtime.sendMessage({
        type: 'manual-archive',
        pdfUrl,
      });
      btn.textContent = res?.ok ? 'Queued to PaperPrism' : 'Failed';
    } catch (err) {
      console.warn('[PaperPrism] archive click failed', err);
      btn.textContent = 'Failed';
    } finally {
      setTimeout(() => {
        btn.disabled = false;
        btn.textContent = 'Save to PaperPrism';
      }, 2500);
    }
  });

  host.appendChild(btn);
}
