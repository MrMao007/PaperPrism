import { useEffect, useState } from 'react';

/**
 * Minimal hash-based router. Keeps Dashboard a single-bundle SPA without
 * pulling in react-router or similar. Routes we care about:
 *   #/            -> papers list
 *   #/topics      -> topics list
 *   #/topics/<slug> -> topic detail
 */
export type Route =
  | { name: 'papers' }
  | { name: 'topics' }
  | { name: 'topic'; slug: string };

export function parseHash(hash: string): Route {
  // Strip leading '#/' or '#'
  const raw = hash.replace(/^#\/?/, '');
  if (!raw) return { name: 'papers' };
  const parts = raw.split('/').filter(Boolean);
  if (parts[0] === 'topics') {
    if (parts.length >= 2) {
      return { name: 'topic', slug: decodeURIComponent(parts[1]) };
    }
    return { name: 'topics' };
  }
  return { name: 'papers' };
}

export function useHashRoute(): Route {
  const [route, setRoute] = useState<Route>(() =>
    typeof window === 'undefined' ? { name: 'papers' } : parseHash(window.location.hash),
  );
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);
  return route;
}

export function navigate(hash: string): void {
  if (typeof window === 'undefined') return;
  window.location.hash = hash;
}
