export function contactDisplayName(c: { name: string; email: string }): string {
  if (!c.name || c.name === 'Contact') {
    const local = c.email.split('@')[0]
      .replace(/[._\-]/g, ' ')
      .replace(/\d+/g, ' ')
      .trim()
    const words = local.split(/\s+/).filter(Boolean)
    if (words.length > 0) {
      return words.map(w => w[0].toUpperCase() + w.slice(1)).join(' ')
    }
    return c.email
  }
  return c.name
}

export function isGenericName(name: string): boolean {
  return !name || name === 'Contact'
}

// Shared-inbox contacts carry a machine-readable suffix on their designation
// ("Talent/Recruiting (role inbox)", "… (unverified guess)") that the backend
// uses to pick the email template and rank results. It's internal plumbing —
// strip the parenthetical for display only. The raw designation is still used
// for avatar color and template keys, so pass the ORIGINAL to those.
export function displayDesignation(designation: string | null | undefined): string {
  return (designation || '')
    .replace(/\s*\((?:role inbox|unverified guess)\)\s*$/i, '')
    .trim()
}

// ── Card trust line: provenance + freshness + company link ────────────────────

const _FREEMAIL = new Set([
  'gmail.com', 'googlemail.com', 'yahoo.com', 'outlook.com', 'hotmail.com',
  'live.com', 'icloud.com', 'me.com', 'proton.me', 'protonmail.com', 'aol.com',
  'gmx.com', 'msn.com', 'mail.com', 'yandex.com', 'zoho.com',
])

// Pull the concrete open role out of the stored provenance note, e.g.
// "Acme is actively hiring for 'Backend Engineer (Payments)' (via Greenhouse)"
// → "Backend Engineer (Payments)". Null when the note names no specific role
// (careers-inbox leads carry no context) — we never dump the raw note on a card.
export function leadRole(context: string | null | undefined): string | null {
  // Backend always wraps the title in SINGLE quotes ("... hiring for 'X'"), so
  // match the single-quote delimiter and allow apostrophes inside the title —
  // the old [^'"] class stopped at the first apostrophe, truncating
  // "Women's Health Engineer" to "Women". Lazy up to the closing '<space|paren|EOL>.
  const m = (context || '').match(/hiring for '(.{3,80}?)'(?=\s|\)|$)/i)
  return m ? m[1].trim() : null
}

// Human source label for "via X". Strips the "/slug" the ATS scrapers append
// and hides internal/synthetic sources that aren't a real discovery signal.
export function leadSource(source: string | null | undefined): string | null {
  const s = (source || '').split('/')[0].trim()
  if (!s || /^(careers-inbox|hunter\.io)$/i.test(s)) return null
  return s
}

// Company website derived from a corporate email domain (careers@acme.com →
// https://acme.com). Null for freemail — a personal address says nothing about
// a company site, and we never guess one.
export function companyWebsite(email: string | null | undefined): string | null {
  const domain = (email || '').split('@')[1]?.toLowerCase().trim()
  if (!domain || domain.indexOf('.') < 0 || _FREEMAIL.has(domain)) return null
  return `https://${domain}`
}

// Compact "found N ago" from an ISO timestamp (backend sends naive UTC).
export function timeAgo(iso: string | null | undefined): string | null {
  if (!iso) return null
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
  const s = (Date.now() - d.getTime()) / 1000
  if (!Number.isFinite(s) || s < 0) return null
  if (s < 90) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}
