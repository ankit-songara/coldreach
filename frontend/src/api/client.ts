import axios, { AxiosError } from 'axios'

const TOKEN_KEY = 'coldreach-token'

export const getToken = () => localStorage.getItem(TOKEN_KEY)
export const setToken = (t: string | null) => {
  if (t) localStorage.setItem(TOKEN_KEY, t)
  else localStorage.removeItem(TOKEN_KEY)
}

// Dev: requests proxy through Vite → FastAPI (see vite.config.ts).
// Prod: VITE_API_URL points to the deployed backend, e.g.
//   VITE_API_URL=https://coldreach-api.vercel.app/api
const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL ?? '/api',
  headers: { 'Content-Type': 'application/json' },
})

// Attach the bearer token to every request
api.interceptors.request.use(config => {
  const token = getToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

/**
 * Normalize any axios failure into ONE human-readable message.
 * Components can always `toast.error(e.message)` — never a raw axios error,
 * never `[object Object]` from a FastAPI validation payload.
 */
function friendlyMessage(err: AxiosError<any>): string {
  // No response at all — network down, server unreachable, CORS, timeout.
  if (!err.response) {
    if (err.code === 'ERR_CANCELED') return 'Request cancelled.'
    if (err.code === 'ECONNABORTED') return 'The request timed out. Please try again.'
    return "Can't reach the server. Check your connection and try again."
  }

  const { status, data } = err.response
  const detail = data?.detail

  // FastAPI HTTPException → detail is a plain string.
  if (typeof detail === 'string' && detail.trim()) return detail

  // FastAPI validation error (422) → detail is an array of {loc, msg, ...}.
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0]
    const field = Array.isArray(first?.loc) ? String(first.loc[first.loc.length - 1]) : ''
    const msg = typeof first?.msg === 'string' ? first.msg : 'has an invalid value'
    return field ? `${field.replace(/_/g, ' ')}: ${msg}` : msg
  }

  if (status === 401) return 'Your session has expired. Please log in again.'
  if (status === 403) return "You don't have access to that."
  if (status === 404) return "That item couldn't be found — it may have been deleted."
  if (status === 429) return 'Slow down a little — too many requests. Try again shortly.'
  if (status >= 500) return 'Something went wrong on our side. Please try again in a moment.'

  return err.message || 'Request failed. Please try again.'
}

api.interceptors.response.use(
  res => res,
  (err: AxiosError<any>) => {
    // Session expired / invalid → drop token and bounce to login
    if (err.response?.status === 401 && getToken()) {
      setToken(null)
      window.dispatchEvent(new Event('coldreach:logout'))
    }
    const wrapped = new Error(friendlyMessage(err))
    // Preserve the status for callers that branch on it (rare, but cheap).
    ;(wrapped as any).status = err.response?.status
    return Promise.reject(wrapped)
  },
)

export default api
