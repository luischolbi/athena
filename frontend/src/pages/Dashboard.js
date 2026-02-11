import { useState, useEffect, useCallback } from 'react';
import TopBar from '../components/TopBar';
import FilterBar from '../components/FilterBar';
import CompanyCard from '../components/CompanyCard';
import Footer from '../components/Footer';
import { fetchStats, fetchFilters, fetchSignals } from '../api';

const PAGE_SIZE = 30;

function SkeletonCard() {
  return (
    <div className="flex items-start gap-4 p-4 rounded-xl bg-athena-card border border-athena-border animate-pulse">
      <div className="w-10 h-10 rounded-xl bg-white/5" />
      <div className="flex-1 space-y-2">
        <div className="h-4 w-48 bg-white/5 rounded" />
        <div className="h-3 w-full bg-white/[0.03] rounded" />
        <div className="flex gap-2 mt-1">
          <div className="h-5 w-16 bg-white/[0.03] rounded" />
          <div className="h-5 w-20 bg-white/[0.03] rounded" />
        </div>
      </div>
      <div className="space-y-1.5">
        <div className="h-5 w-24 bg-white/[0.03] rounded" />
      </div>
    </div>
  );
}

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const [filterOptions, setFilterOptions] = useState({});
  const [filters, setFilters] = useState({});
  const [search, setSearch] = useState('');
  const [companies, setCompanies] = useState([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [expandedId, setExpandedId] = useState(null);
  const [error, setError] = useState(null);

  // Initial load
  useEffect(() => {
    fetchStats().then(setStats).catch(() => {});
    fetchFilters().then(setFilterOptions).catch(() => {});
  }, []);

  // Fetch companies when filters/search change
  const loadCompanies = useCallback(async (newOffset = 0, append = false) => {
    if (append) {
      setLoadingMore(true);
    } else {
      setLoading(true);
      setError(null);
    }

    try {
      const data = await fetchSignals({
        ...filters,
        search: search || undefined,
        limit: PAGE_SIZE,
        offset: newOffset,
      });
      if (append) {
        setCompanies((prev) => [...prev, ...data.results]);
      } else {
        setCompanies(data.results);
        setExpandedId(null);
      }
      setTotal(data.total);
      setOffset(newOffset);
    } catch (err) {
      console.error('Failed to fetch:', err);
      if (!append) setError('Could not load data — is the API running?');
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, [filters, search]);

  useEffect(() => {
    loadCompanies(0, false);
  }, [loadCompanies]);

  function handleFilterChange(key, value) {
    setFilters((prev) => ({ ...prev, [key]: value || undefined }));
  }

  function handleCardClick(company) {
    setExpandedId((prev) => prev === company.id ? null : company.id);
  }

  function handleLoadMore() {
    loadCompanies(offset + PAGE_SIZE, true);
  }

  // Close expanded on Escape
  useEffect(() => {
    function onKey(e) { if (e.key === 'Escape') setExpandedId(null); }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const hasMore = companies.length < total;

  return (
    <div className="min-h-screen bg-athena-bg pt-14">
      <TopBar stats={stats} />

      <FilterBar
        filters={filters}
        filterOptions={filterOptions}
        onFilterChange={handleFilterChange}
        total={total}
        showing={companies.length}
        onSearchChange={setSearch}
      />

      <main className="max-w-[1440px] mx-auto px-6 pt-5 pb-4">
        {loading ? (
          <div className="space-y-2.5">
            {Array.from({ length: 8 }).map((_, i) => <SkeletonCard key={i} />)}
          </div>
        ) : error ? (
          <div className="flex flex-col items-center justify-center py-24 text-athena-muted">
            <div className="w-10 h-10 rounded-xl bg-red-500/10 flex items-center justify-center mb-3">
              <span className="text-red-400 text-lg">!</span>
            </div>
            <p className="font-sans text-sm">{error}</p>
          </div>
        ) : companies.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-24 text-athena-muted">
            <div className="w-10 h-10 rounded-xl bg-white/5 flex items-center justify-center mb-3">
              <svg className="w-5 h-5 opacity-40" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </div>
            <p className="font-sans text-sm">No companies match these filters</p>
            <p className="font-sans text-xs text-athena-muted/50 mt-1">Try broadening your search</p>
          </div>
        ) : (
          <>
            <div className="space-y-2.5">
              {companies.map((c, i) => (
                <CompanyCard
                  key={c.id}
                  company={c}
                  isExpanded={expandedId === c.id}
                  onClick={handleCardClick}
                  delay={Math.min(i * 30, 300)}
                />
              ))}
            </div>

            {hasMore && (
              <div className="flex justify-center py-8">
                <button
                  onClick={handleLoadMore}
                  disabled={loadingMore}
                  className="px-5 py-2 rounded-lg bg-athena-card border border-athena-border text-[13px] font-medium
                             text-athena-muted hover:text-athena-accent hover:border-athena-accent/30
                             disabled:opacity-40 disabled:cursor-not-allowed font-sans transition-colors"
                >
                  {loadingMore ? (
                    <span className="flex items-center gap-2">
                      <svg className="animate-spin w-3.5 h-3.5" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                      </svg>
                      Loading...
                    </span>
                  ) : (
                    `Load more (${(total - companies.length).toLocaleString()} remaining)`
                  )}
                </button>
              </div>
            )}
          </>
        )}
      </main>

      <Footer />
    </div>
  );
}
