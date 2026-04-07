import axios from 'axios';

const api = axios.create({
  baseURL: process.env.REACT_APP_API_URL || '',
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

export async function fetchNewCompanies(params = {}) {
  const query = {};
  if (params.status) query.status = params.status;
  if (params.include_recent) query.include_recent = true;
  if (params.limit) query.limit = params.limit;
  if (params.offset) query.offset = params.offset;
  console.log('[fetchNewCompanies] request params:', JSON.stringify(query));
  const { data } = await api.get('/api/new', { params: query });
  console.log('[fetchNewCompanies] response:', { total: data.total, new_count: data.new_count, recent_count: data.recent_count, resultCount: data.results?.length });
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

export async function fetchPipeline() {
  const { data } = await api.get('/api/pipeline');
  return data;
}

export async function addToPipeline(companyId, addedBy = 'scout') {
  const { data } = await api.post('/api/pipeline/add', { company_id: companyId, added_by: addedBy });
  return data;
}

export async function moveInPipeline(companyId, status, position = 0) {
  const { data } = await api.put('/api/pipeline/move', { company_id: companyId, status, position });
  return data;
}

export async function addPipelineNote(companyId, content, author = 'scout', authorRole = 'scout') {
  const { data } = await api.post('/api/pipeline/note', { company_id: companyId, author, author_role: authorRole, content });
  return data;
}

export async function removeFromPipeline(companyId) {
  const { data } = await api.delete(`/api/pipeline/${companyId}`);
  return data;
}

export async function quickScreen(website, companyName) {
  const { data } = await api.post('/api/quick-screen', { website, company_name: companyName });
  return data;
}
