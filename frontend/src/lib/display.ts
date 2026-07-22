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
