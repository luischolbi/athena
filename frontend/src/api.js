import axios from 'axios';

const api = axios.create({
  baseURL: process.env.REACT_APP_API_URL || 'http://127.0.0.1:8000',
  timeout: 30000,
});

export async function fetchStats() {
  const { data } = await api.get('/api/stats');
  return data;
}

export async function fetchFilters() {
  const { data } = await api.get('/api/filters');
  return data;
}

export async function fetchSignals(params = {}) {
  const query = {};
  if (params.program) query.program = params.program;
  if (params.source) query.source = params.source;
  if (params.sector) query.sector = params.sector;
  if (params.geography) query.geography = params.geography;
  if (params.min_score) query.min_score = params.min_score;
  if (params.stage) query.stage = params.stage;
  if (params.cohort_year) query.cohort_year = params.cohort_year;
  if (params.search) query.search = params.search;
  if (params.sort) query.sort = params.sort;
  if (params.hide_inactive) query.hide_inactive = true;
  if (params.hide_unverified) query.hide_unverified = true;
  if (params.data_tier) query.data_tier = params.data_tier;
  query.limit = params.limit || 30;
  query.offset = params.offset || 0;

  const { data } = await api.get('/api/signals', { params: query });
  return data;
}

export async function fetchCompany(id) {
  const { data } = await api.get(`/api/company/${id}`);
  return data;
}

export async function fetchTop20() {
  const { data } = await api.get('/api/top20');
  return data;
}

export async function createFounder(companyId, founder) {
  const { data } = await api.post(`/api/companies/${companyId}/founders`, founder);
  return data;
}

export async function updateFounder(founderId, updates) {
  const { data } = await api.put(`/api/founders/${founderId}`, updates);
  return data;
}

export async function deleteFounder(founderId) {
  const { data } = await api.delete(`/api/founders/${founderId}`);
  return data;
}
