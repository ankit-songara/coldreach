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
