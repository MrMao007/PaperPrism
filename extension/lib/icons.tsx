/**
 * PaperPrism icon set — small collection of single-stroke SVGs in the
 * "Observatory Console" visual language (lucide-inspired, currentColor,
 * stroke-width 1.75, square 24x24 viewBox).  Inlined as JSX so the
 * extension does not pull in an icon library at runtime.
 *
 * Usage:
 *   <Icon name="download" size={14} />
 *   <Icon name="trash" size={16} aria-hidden />
 *
 * All paths use stroke + currentColor only — never fill — so the icon
 * inherits the surrounding button's text color and the accent rail
 * tinting just works across ghost / primary / danger variants.
 */
import { type CSSProperties } from 'react';

/* eslint-disable react/jsx-key */
const PATHS: Record<string, JSX.Element> = {
  download: (
    <>
      <path d="M12 4v11" />
      <path d="m7 11 5 5 5-5" />
      <path d="M5 19h14" />
    </>
  ),
  trash: (
    <>
      <path d="M4 7h16" />
      <path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
      <path d="M6 7v12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
    </>
  ),
  'external-link': (
    <>
      <path d="M14 4h6v6" />
      <path d="M20 4 10 14" />
      <path d="M19 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" />
    </>
  ),
  plus: (
    <>
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </>
  ),
  x: (
    <>
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </>
  ),
  'chevron-left': <path d="m15 6-6 6 6 6" />,
  'chevron-right': <path d="m9 6 6 6-6 6" />,
  'folder-plus': (
    <>
      <path d="M4 7a2 2 0 0 1 2-2h3l2 2h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Z" />
      <path d="M12 11v6" />
      <path d="M9 14h6" />
    </>
  ),
  filter: (
    <path d="M3 5h18l-7 9v6l-4-2v-4Z" />
  ),
  check: <path d="m5 12 4.5 4.5L19 7" />,
  layers: (
    <>
      <path d="m12 3 9 5-9 5-9-5 9-5Z" />
      <path d="m3 13 9 5 9-5" />
      <path d="m3 17 9 5 9-5" />
    </>
  ),
  sparkles: (
    <>
      <path d="M12 4v3" />
      <path d="M12 17v3" />
      <path d="M4 12h3" />
      <path d="M17 12h3" />
      <path d="m6.5 6.5 2 2" />
      <path d="m15.5 15.5 2 2" />
      <path d="m6.5 17.5 2-2" />
      <path d="m15.5 8.5 2-2" />
    </>
  ),
  'arrow-up-right': (
    <>
      <path d="M7 17 17 7" />
      <path d="M8 7h9v9" />
    </>
  ),
  eye: (
    <>
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
};

export type IconName = keyof typeof PATHS;

interface IconProps {
  name: IconName;
  size?: number;
  strokeWidth?: number;
  className?: string;
  style?: CSSProperties;
  'aria-hidden'?: boolean;
  'aria-label'?: string;
  title?: string;
}

export function Icon({
  name,
  size = 16,
  strokeWidth = 1.75,
  className,
  style,
  title,
  ...aria
}: IconProps) {
  const path = PATHS[name];
  if (!path) {
    // Fail loud during dev; in prod the icon just renders nothing rather
    // than crashing the page.
    if (process.env.NODE_ENV !== 'production') {
      console.warn(`[Icon] unknown icon "${name}"`);
    }
    return null;
  }
  // If no aria-label/title is given, mark as decorative for screen readers.
  const decorative = !aria['aria-label'] && !title;
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={style}
      aria-hidden={decorative ? true : aria['aria-hidden']}
      aria-label={aria['aria-label']}
      role={aria['aria-label'] ? 'img' : undefined}
      focusable={false}
    >
      {title ? <title>{title}</title> : null}
      {path}
    </svg>
  );
}
