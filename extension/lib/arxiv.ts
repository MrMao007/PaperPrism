/**
 * arxiv URL / filename parsing utilities.
 *
 * Supports two arxiv ID schemes:
 *   - new:    YYMM.NNNNN (4-5 digits, optional version)  e.g. 2604.01234, 2604.01234v2
 *   - legacy: archive.subject/YYMMNNN                    e.g. hep-th/9901001, cs.LG/0512001
 *
 * Accepted URL forms:
 *   - https://arxiv.org/pdf/2604.01234
 *   - https://arxiv.org/pdf/2604.01234v2
 *   - https://arxiv.org/pdf/2604.01234.pdf
 *   - https://arxiv.org/pdf/2604.01234v2.pdf
 *   - https://arxiv.org/abs/2604.01234
 *   - https://arxiv.org/pdf/cs.LG/0512001v1
 *   - http(s) and with/without `www.` prefix
 */

export interface ArxivId {
  /** Canonical ID without version, e.g. "2604.01234" */
  id: string;
  /** Version tag like "v2", or undefined if not specified */
  version?: string;
  /** Full id with version, used for filenames, e.g. "2604.01234v2" */
  fullId: string;
  /** Whether the ID is the legacy "archive/num" form */
  legacy: boolean;
}

const NEW_RE = /(\d{4}\.\d{4,5})(v\d+)?/;
const LEGACY_RE = /([a-z\-]+(?:\.[A-Z]{2})?\/\d{7})(v\d+)?/i;

export function parseArxivIdFromString(input: string): ArxivId | null {
  if (!input) return null;

  const newMatch = input.match(NEW_RE);
  if (newMatch) {
    const id = newMatch[1];
    const version = newMatch[2];
    return {
      id,
      version,
      fullId: version ? `${id}${version}` : id,
      legacy: false,
    };
  }

  const legacyMatch = input.match(LEGACY_RE);
  if (legacyMatch) {
    const id = legacyMatch[1];
    const version = legacyMatch[2];
    return {
      id,
      version,
      fullId: version ? `${id}${version}` : id,
      legacy: true,
    };
  }

  return null;
}

export function isArxivHost(url: string): boolean {
  try {
    const u = new URL(url);
    return /(^|\.)arxiv\.org$/i.test(u.hostname);
  } catch {
    return false;
  }
}

export function isArxivPdfUrl(url: string): boolean {
  if (!isArxivHost(url)) return false;
  try {
    const u = new URL(url);
    // /pdf/<id>[.pdf] covers the canonical PDF endpoint.
    return /^\/pdf\//i.test(u.pathname);
  } catch {
    return false;
  }
}

export function absUrlFromId(id: ArxivId): string {
  return `https://arxiv.org/abs/${id.fullId}`;
}

export function pdfUrlFromId(id: ArxivId): string {
  return `https://arxiv.org/pdf/${id.fullId}`;
}

/**
 * Sanitize an arxiv id so it is safe to use as a filename segment.
 * Legacy ids contain a "/" which must be flattened.
 */
export function safeFilename(id: ArxivId): string {
  return id.fullId.replace(/[\\/:*?"<>|]/g, '_');
}
