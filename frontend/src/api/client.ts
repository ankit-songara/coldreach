import axios from 'axios'

const TOKEN_KEY = 'coldreach-token'

export const getToken = () => localStorage.getItem(TOKEN_KEY)
export const setToken = (t: string | null) => {
  if (t) localStorage.setItem(TOKEN_KEY, t)
  else localStorage.removeItem(TOKEN_KEY)
}

// All requests proxy through Vite → FastAPI (see vite.config.ts)
const api = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
})

// Attach the bearer token to every request
api.interceptors.request.use(config => {
  const token = getToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  res => res,
  err => {
    // Session expired / invalid → drop token and bounce to login
    if (err.response?.status === 401 && getToken()) {
      setToken(null)
      window.dispatchEvent(new Event('coldreach:logout'))
    }
    const msg = err.response?.data?.detail || err.message || 'Request failed'
    return Promise.reject(new Error(msg))
  },
)

export default api
