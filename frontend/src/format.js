export const LANG_NAMES = { en: "English", ta: "Tamil", hi: "Hindi" };

export const EXAMPLES = [
  ["Beach outfit", "outfit for the beach this summer"],
  ["Tamil", "கோடை கடற்கரைக்கு ஏற்ற ஆடை"],
  ["Hindi", "सर्दियों के लिए गर्म जैकेट"],
  ["Wedding, under $60", "outfit for a summer wedding for men under $60"],
  ["All-day comfort", "comfortable shoes for standing all day at work"],
];

export const DEPARTMENTS = ["Womens", "Mens", "Unisex-adult", "Girls", "Boys"];

export const EVENT_LABELS = {
  "product.created": "new product",
  "product.updated": "text changed",
  "product.enriched": "LLM enrichment saved",
  "product.changed": "price/details changed",
  "product.reviewed": "new review",
  "product.deactivated": "removed",
};

export function statusLabel(stt = {}) {
  const secs = ((stt.latency_ms || 0) / 1000).toFixed(1);
  const labels = {
    indexed: `embedded, searchable after ${secs}s`,
    payload: `updated after ${secs}s, no re-embedding`,
    removed: `removed from search after ${secs}s`,
    failed: "indexing failed (see worker)",
    skipped: "skipped (inactive)",
  };
  return labels[stt.state] || "waiting for the indexer…";
}

export const fmtInt = (n) => Number(n || 0).toLocaleString("en-US");

// Python's {:g}: 60.0 -> "60", 59.5 -> "59.5"
export const fmtG = (n) => String(Number(n));

// Python's str.capitalize(): first letter upper, rest lower.
export const capitalize = (s) => {
  const t = String(s || "");
  return t.charAt(0).toUpperCase() + t.slice(1).toLowerCase();
};

export function buildChips(parsed) {
  const chips = [];
  if (parsed.language) chips.push(`language: ${LANG_NAMES[parsed.language] || parsed.language}`);
  if (parsed.gender) chips.push(`for ${parsed.gender}`);
  if (parsed.max_price) chips.push(`under $${fmtG(parsed.max_price)}`);
  if (parsed.min_price) chips.push(`over $${fmtG(parsed.min_price)}`);
  for (const key of ["occasions", "seasons", "colors"]) {
    for (const v of parsed[key] || []) chips.push(String(v).replaceAll("_", " "));
  }
  for (const s of parsed.slots || []) chips.push(`look for: ${s.name}`);
  return chips;
}
