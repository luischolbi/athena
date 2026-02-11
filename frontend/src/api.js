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
  query.limit = params.limit || 30;
  query.offset = params.offset || 0;

  const { data } = await api.get('/api/signals', { params: query });
  return data;
}

export async function fetchCompany(id) {
  const { data } = await api.get(`/api/company/${id}`);
  return data;
}
