import api from './client'
import type { Contact, ContactStatus } from '../types'

export const contactsApi = {
  list:      ()                        => api.get<Contact[]>('/contacts').then(r => r.data),
  create:    (d: Partial<Contact>)     => api.post<Contact>('/contacts', d).then(r => r.data),
  update:    (id: number, d: Partial<Contact>) => api.patch<Contact>(`/contacts/${id}`, d).then(r => r.data),
  setStatus: (id: number, status: ContactStatus) => api.patch<Contact>(`/contacts/${id}`, { status }).then(r => r.data),
  delete:    (id: number)              => api.delete(`/contacts/${id}`),
  deleteAll: ()                        => api.delete('/contacts'),
}
