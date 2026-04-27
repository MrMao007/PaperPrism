/**
 * Settings stored in chrome.storage.local.
 * Keep this shape stable across versions; migrations go in `migrate()`.
 */

export interface Settings {
  /**
   * Hidden workspace path the Agent uses to copy papers into.
   * The extension itself cannot write here -- it's a UI hint forwarded to
   * the Agent so both sides stay in sync.
   * Default: `~/.paperprism/vault` (dot-prefixed = hidden on macOS/Linux).
   */
  vaultPathHint: string;
  /** Local PaperPrism Agent base URL. */
  agentBaseUrl: string;
  /** Auth token shared with the Agent. Empty = disabled. */
  agentToken: string;
  /**
   * Whether to notify the Agent to mirror finished arxiv downloads into
   * the hidden vault. Disable to keep the extension passive.
   */
  archiveEnabled: boolean;
  /** Whether to show a native notification after each archive event. */
  notifyEnabled: boolean;
}

export const DEFAULT_SETTINGS: Settings = {
  vaultPathHint: '~/.paperprism/vault',
  agentBaseUrl: 'http://127.0.0.1:17321',
  agentToken: '',
  archiveEnabled: true,
  notifyEnabled: true,
};

const KEY = 'paperprism.settings.v1';

export async function loadSettings(): Promise<Settings> {
  const got = await chrome.storage.local.get(KEY);
  const stored = (got[KEY] ?? {}) as Partial<Settings>;
  return { ...DEFAULT_SETTINGS, ...stored };
}

export async function saveSettings(next: Partial<Settings>): Promise<Settings> {
  const current = await loadSettings();
  const merged: Settings = { ...current, ...next };
  await chrome.storage.local.set({ [KEY]: merged });
  return merged;
}

export function onSettingsChanged(
  cb: (settings: Settings) => void,
): () => void {
  const listener = (
    changes: Record<string, chrome.storage.StorageChange>,
    area: chrome.storage.AreaName,
  ) => {
    if (area !== 'local' || !(KEY in changes)) return;
    const next = { ...DEFAULT_SETTINGS, ...(changes[KEY].newValue ?? {}) };
    cb(next as Settings);
  };
  chrome.storage.onChanged.addListener(listener);
  return () => chrome.storage.onChanged.removeListener(listener);
}
