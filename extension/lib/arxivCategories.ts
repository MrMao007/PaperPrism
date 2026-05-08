/**
 * arXiv category taxonomy — curated subset for Atlas feed configuration.
 *
 * Each entry maps a user-friendly name to the arXiv category ID used in
 * the RSS URL (e.g. `http://export.arxiv.org/rss/cs.AI`).
 *
 * Source: https://arxiv.org/category_taxonomy
 */
export interface ArxivCategory {
  id: string;       // e.g. "cs.AI"
  label: string;    // e.g. "Artificial Intelligence"
  group: string;    // e.g. "Computer Science"
}

export const ARXIV_CATEGORIES: ArxivCategory[] = [
  // ── Computer Science ──
  { id: "cs.AI", label: "Artificial Intelligence", group: "Computer Science" },
  { id: "cs.CL", label: "Computation & Language (NLP)", group: "Computer Science" },
  { id: "cs.CV", label: "Computer Vision & Pattern Recognition", group: "Computer Science" },
  { id: "cs.LG", label: "Machine Learning", group: "Computer Science" },
  { id: "cs.NE", label: "Neural & Evolutionary Computing", group: "Computer Science" },
  { id: "cs.RO", label: "Robotics", group: "Computer Science" },
  { id: "cs.CR", label: "Cryptography & Security", group: "Computer Science" },
  { id: "cs.DB", label: "Databases", group: "Computer Science" },
  { id: "cs.DC", label: "Distributed & Cluster Computing", group: "Computer Science" },
  { id: "cs.DS", label: "Data Structures & Algorithms", group: "Computer Science" },
  { id: "cs.HC", label: "Human-Computer Interaction", group: "Computer Science" },
  { id: "cs.IR", label: "Information Retrieval", group: "Computer Science" },
  { id: "cs.IT", label: "Information Theory", group: "Computer Science" },
  { id: "cs.MA", label: "Multiagent Systems", group: "Computer Science" },
  { id: "cs.MM", label: "Multimedia", group: "Computer Science" },
  { id: "cs.NI", label: "Networking & Internet Architecture", group: "Computer Science" },
  { id: "cs.PL", label: "Programming Languages", group: "Computer Science" },
  { id: "cs.SE", label: "Software Engineering", group: "Computer Science" },
  { id: "cs.SI", label: "Social & Information Networks", group: "Computer Science" },
  { id: "cs.CY", label: "Computers & Society", group: "Computer Science" },

  // ── Mathematics ──
  { id: "math.ST", label: "Statistics Theory", group: "Mathematics" },
  { id: "math.PR", label: "Probability", group: "Mathematics" },
  { id: "math.OC", label: "Optimization & Control", group: "Mathematics" },
  { id: "math.NA", label: "Numerical Analysis", group: "Mathematics" },

  // ── Statistics ──
  { id: "stat.ML", label: "Machine Learning (Stat)", group: "Statistics" },
  { id: "stat.AP", label: "Applied Statistics", group: "Statistics" },
  { id: "stat.CO", label: "Computation (Stat)", group: "Statistics" },

  // ── Physics ──
  { id: "physics.comp-ph", label: "Computational Physics", group: "Physics" },
  { id: "physics.data-an", label: "Data Analysis & Statistics", group: "Physics" },

  // ── Quantitative Biology ──
  { id: "q-bio.NC", label: "Neurons & Cognition", group: "Quantitative Biology" },
  { id: "q-bio.QM", label: "Quantitative Methods (Bio)", group: "Quantitative Biology" },

  // ── Quantitative Finance ──
  { id: "q-fin.CP", label: "Computational Finance", group: "Quantitative Finance" },
  { id: "q-fin.ML", label: "Machine Learning (Finance)", group: "Quantitative Finance" },

  // ── Electrical Engineering ──
  { id: "eess.AS", label: "Audio & Speech Processing", group: "Electrical Engineering" },
  { id: "eess.IV", label: "Image & Video Processing", group: "Electrical Engineering" },
  { id: "eess.SP", label: "Signal Processing", group: "Electrical Engineering" },
];

/** Grouped categories for rendering in UI. */
export function groupedCategories(): Map<string, ArxivCategory[]> {
  const map = new Map<string, ArxivCategory[]>();
  for (const c of ARXIV_CATEGORIES) {
    const list = map.get(c.group) || [];
    list.push(c);
    map.set(c.group, list);
  }
  return map;
}
