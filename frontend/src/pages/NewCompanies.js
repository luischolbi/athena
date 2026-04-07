import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import TopBar from '../components/TopBar';
import CompanyCard from '../components/CompanyCard';
import Footer from '../components/Footer';
import { fetchNewCompanies, fetchStats } from '../api';

function NewnessBadge({ status }) {
  if (status === 'new') {
    return (
      <span className="inline-flex items-center px-1.5 py-0 rounded text-[9px] font-mono text-emerald-400/90 border border-emerald-500/25 leading-relaxed">
        New
      </span>
    );
  }
  if (status === 'recent') {
    return (
      <span className="inline-flex items-center px-1.5 py-0 rounded text-[9px] font-mono text-amber-400/90 border border-amber-500/25 leading-relaxed">
        Recent
      </span>
    );
  }
  return null;
}

function formatSslDate(isoDate) {
  if (!isoDate) return null;
  try {
    const d = new Date(isoDate + 'T00:00:00');
    return d.toLocaleDateString('en-US', { month: 'short', year: 'numeric' });
  } catch {
    return isoDate;
  }
}

function NewCompanyCard({ company, isExpanded, onClick, delay }) {
  return (
    <div>
      <CompanyCard
        company={company}
        isExpanded={isExpanded}
        onClick={onClick}
        delay={delay}
      />
      {/* Overlay newness info on the card */}
      {!isExpanded && (company.newness_status || company.ssl_first_seen) && (
        <div className="flex items-center gap-2 -mt-3 mb-1 ml-16 pl-0.5">
          <NewnessBadge status={company.newness_status} />
          {company.ssl_first_seen && (
            <span className="text-[10px] text-athena-muted/50 font-mono">
              Website live since {formatSslDate(company.ssl_first_seen)}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

export default function NewCompanies() {
  const [tab, setTab] = useState('new');
  const [companies, setCompanies] = useState([]);
  const [stats, setStats] = useState(null);
  const [newCount, setNewCount] = useState(0);
  const [recentCount, setRecentCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [expandedId, setExpandedId] = useState(null);

  useEffect(() => {
    fetchStats().then(setStats).catch(() => {});
  }, []);

  useEffect(() => {
    let cancelled = false;
    console.log(`[NewCompanies] useEffect fired, tab="${tab}"`);
    setLoading(true);
    setError(null);
    setExpandedId(null);
    fetchNewCompanies({ status: tab })
      .then((data) => {
        console.log(`[NewCompanies] .then() tab="${tab}" cancelled=${cancelled} results=${data.results?.length}`);
        if (cancelled) return;
        setCompanies(data.results || []);
        setNewCount(data.new_count || 0);
        setRecentCount(data.recent_count || 0);
      })
      .catch((err) => {
        console.error(`[NewCompanies] .catch() tab="${tab}" cancelled=${cancelled}`, err);
        if (cancelled) return;
        setError('Could not load data');
      })
      .finally(() => {
        console.log(`[NewCompanies] .finally() tab="${tab}" cancelled=${cancelled}`);
        if (cancelled) return;
        setLoading(false);
      });
    return () => { cancelled = true; };
  }, [tab]);

  // Close expanded on Escape
  useEffect(() => {
    function onKey(e) { if (e.key === 'Escape') setExpandedId(null); }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  function handleCardClick(company) {
    setExpandedId((prev) => prev === company.id ? null : company.id);
  }

  const isEmpty = !loading && !error && companies.length === 0;

  return (
    <div className="min-h-screen bg-athena-bg pt-14">
      <TopBar stats={stats} />

      <div className="max-w-[1440px] mx-auto px-6 pt-8 pb-4">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="font-sans font-bold text-[22px] text-athena-text">
              New Companies
            </h1>
            <p className="text-[13px] text-athena-muted mt-1">
              Recently launched companies detected via SSL certificate age
            </p>
          </div>
          <Link
            to="/"
            className="px-4 py-2 rounded-lg bg-athena-card border border-athena-border text-[13px] text-athena-muted
                       hover:text-athena-accent hover:border-athena-accent/30 font-sans transition-colors no-underline"
          >
            &larr; Back to Dashboard
          </Link>
        </div>

        {/* Tabs */}
        <div className="flex items-center gap-1 mb-6 border-b border-athena-border">
          <button
            onClick={() => setTab('new')}
            className={`px-4 py-2.5 text-[13px] font-medium transition-colors border-b-2 -mb-px ${
              tab === 'new'
                ? 'text-athena-accent border-athena-accent'
                : 'text-athena-muted border-transparent hover:text-athena-text'
            }`}
          >
            New{newCount > 0 ? ` (${newCount})` : ''}
          </button>
          <button
            onClick={() => setTab('recent')}
            className={`px-4 py-2.5 text-[13px] font-medium transition-colors border-b-2 -mb-px ${
              tab === 'recent'
                ? 'text-athena-accent border-athena-accent'
                : 'text-athena-muted border-transparent hover:text-athena-text'
            }`}
          >
            Recent{recentCount > 0 ? ` (${recentCount})` : ''}
          </button>
        </div>

        {/* Content */}
        {loading ? (
          <div className="space-y-3">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="h-20 rounded-xl bg-athena-card border border-athena-border animate-pulse" />
            ))}
          </div>
        ) : error ? (
          <div className="flex flex-col items-center justify-center py-24 text-athena-muted">
            <div className="w-10 h-10 rounded-xl bg-red-500/10 flex items-center justify-center mb-3">
              <span className="text-red-400 text-lg">!</span>
            </div>
            <p className="font-sans text-sm">{error}</p>
          </div>
        ) : isEmpty ? (
          <div className="flex flex-col items-center justify-center py-24 text-athena-muted">
            <div className="w-10 h-10 rounded-xl bg-white/5 flex items-center justify-center mb-3">
              <svg className="w-5 h-5 opacity-40" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6m0 0v6m0-6h6m-6 0H6" />
              </svg>
            </div>
            <p className="font-sans text-sm">No {tab} companies detected yet.</p>
            <p className="font-sans text-xs text-athena-muted/50 mt-1">
              Run <code className="font-mono bg-white/5 px-1.5 py-0.5 rounded">python enrich_newness.py</code> to check.
            </p>
          </div>
        ) : (
          <div className="space-y-2.5">
            {companies.map((c, i) => (
              <NewCompanyCard
                key={c.id}
                company={c}
                isExpanded={expandedId === c.id}
                onClick={handleCardClick}
                delay={Math.min(i * 30, 300)}
              />
            ))}
          </div>
        )}
      </div>

      <Footer />
    </div>
  );
}
