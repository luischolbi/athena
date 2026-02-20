import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import TopBar from '../components/TopBar';
import Footer from '../components/Footer';
import { fetchTop20, fetchStats } from '../api';

function ScoreBar({ label, score, max }) {
  const isNull = score == null;
  const s = isNull ? 0 : score;
  const pct = max > 0 ? (s / max) * 100 : 0;
  let barColor;
  if (isNull) barColor = 'bg-transparent';
  else if (pct >= 75) barColor = 'bg-athena-accent';
  else if (pct >= 50) barColor = 'bg-amber-400';
  else if (pct > 0) barColor = 'bg-athena-muted/40';
  else barColor = 'bg-transparent';

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-medium text-athena-text">{label}</span>
        <span className="font-mono text-[11px] text-athena-muted">{isNull ? '—' : `${s}/${max}`}</span>
      </div>
      <div className="h-1 rounded-full bg-white/5 overflow-hidden">
        <div className={`h-full rounded-full ${barColor}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function RankCard({ company, rank }) {
  const [expanded, setExpanded] = useState(false);
  const bd = company.score_breakdown || {};
  const components = bd.components;
  const founders = company.founders || [];

  const score = company.athena_score != null ? company.athena_score : 0;
  const priority = company.priority || bd.priority || 'low';

  let scoreBg, scoreText;
  if (score >= 4.0) {
    scoreBg = 'bg-emerald-500/20';
    scoreText = 'text-emerald-400';
  } else if (score >= 3.5) {
    scoreBg = 'bg-blue-500/20';
    scoreText = 'text-blue-400';
  } else if (score >= 3.0) {
    scoreBg = 'bg-amber-500/10';
    scoreText = 'text-amber-400';
  } else {
    scoreBg = 'bg-white/5';
    scoreText = 'text-athena-muted';
  }

  return (
    <div
      className="card-enter"
      style={{ animationDelay: `${rank * 40}ms` }}
    >
      <div
        onClick={() => setExpanded(!expanded)}
        className={`rounded-xl border cursor-pointer transition-all duration-200
          ${expanded
            ? 'bg-athena-card-hover border-athena-accent/30 shadow-[0_0_20px_rgba(59,130,246,0.06)]'
            : 'bg-athena-card border-athena-border hover:border-athena-border-hover hover:bg-athena-card-hover'
          }`}
      >
        <div className="flex items-center gap-4 p-4">
          {/* Rank number */}
          <div className="flex-shrink-0 w-8 text-center">
            <span className={`font-mono font-bold text-[18px] ${
              rank <= 3 ? 'text-athena-accent' : 'text-athena-muted/40'
            }`}>
              {rank}
            </span>
          </div>

          {/* Score badge */}
          <div className={`flex-shrink-0 w-14 h-12 rounded-xl flex items-center justify-center ${scoreBg}`}>
            <span className={`font-mono font-bold text-[16px] ${scoreText}`}>{score.toFixed(1)}</span>
          </div>

          {/* Company info */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <h3 className="font-sans font-semibold text-athena-text text-[15px] truncate">
                {company.name}
              </h3>
              {company.data_tier === 1 && (
                <span className="px-1.5 py-0 rounded text-[9px] font-mono text-emerald-400/80 border border-emerald-500/20">
                  Curated
                </span>
              )}
              {priority === 'high' && (
                <span className="px-1.5 py-0 rounded text-[9px] font-mono text-emerald-400/80 border border-emerald-500/20">High Priority</span>
              )}
              {priority === 'investigate' && (
                <span className="px-1.5 py-0 rounded text-[9px] font-mono text-blue-400/80 border border-blue-500/20">Investigate</span>
              )}
              {company.is_cross_layer && (
                <span className="text-[10px] text-athena-accent" title="Cross-layer match">✦</span>
              )}
              {company.cohort && (
                <span className="px-1.5 py-0 rounded text-[9px] font-mono text-violet-400/80 border border-violet-500/20">
                  {company.cohort}
                </span>
              )}
            </div>
            <p className="text-[12px] text-athena-muted mt-0.5 line-clamp-1">
              {company.description || 'No description'}
            </p>
            <div className="flex items-center gap-2 mt-1.5 text-[11px] text-athena-muted">
              {company.sector && (
                <span className="px-1.5 py-0.5 rounded border border-athena-border text-[10px]">
                  {company.sector}
                </span>
              )}
              {company.geography && (
                <span className="text-athena-muted/60">{company.geography}</span>
              )}
              {company.stage && (
                <span className="font-mono text-athena-muted/50">{company.stage}</span>
              )}
            </div>
          </div>

          {/* Score mini-breakdown */}
          {components && (
            <div className="hidden lg:flex items-center gap-3 flex-shrink-0">
              {[
                { key: 'thesis', label: 'Thesis' },
                { key: 'team', label: 'Team' },
                { key: 'program', label: 'Prog' },
                { key: 'traction', label: 'Tract' },
                { key: 'data', label: 'Data' },
              ].map(({ key, label }) => {
                const comp = components[key];
                if (!comp) return null;
                const s = comp.score;
                const isNull = s == null;
                return (
                  <div key={key} className="text-center">
                    <div className="font-mono text-[10px] text-athena-muted/50">{label}</div>
                    <div className={`font-mono text-[12px] font-bold ${
                      isNull ? 'text-athena-muted/30' :
                      s === comp.max ? 'text-athena-accent' :
                      s > 0 ? 'text-athena-text' : 'text-athena-muted/30'
                    }`}>
                      {isNull ? '—' : `${s}/${comp.max}`}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          {/* Expand chevron */}
          <svg className={`w-4 h-4 text-athena-muted/40 transition-transform flex-shrink-0 ${expanded ? 'rotate-180' : ''}`}
               fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="mt-1 p-5 rounded-xl bg-athena-card border border-athena-border space-y-4 animate-fade-in">
          {company.description && (
            <p className="text-[13px] text-athena-muted leading-relaxed">{company.description}</p>
          )}

          {company.website && (
            <div className="flex items-center gap-3">
              <a href={company.website} target="_blank" rel="noopener noreferrer"
                 className="text-[13px] text-athena-accent hover:text-athena-accent-hover font-medium"
                 onClick={(e) => e.stopPropagation()}>
                {company.website.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '')} ↗
              </a>
              <span className={`inline-flex items-center gap-1 text-[10px] font-mono ${
                company.company_status === 'active' ? 'text-emerald-400/70' :
                company.company_status === 'inactive' ? 'text-orange-400/70' : 'text-athena-muted/50'
              }`}>
                <span className={`w-1.5 h-1.5 rounded-full ${
                  company.company_status === 'active' ? 'bg-emerald-400' :
                  company.company_status === 'inactive' ? 'bg-orange-400' : 'bg-athena-muted/40'
                }`} />
                {company.company_status || 'Unknown'}
              </span>
            </div>
          )}

          <div className="grid md:grid-cols-2 gap-4">
            {/* Score breakdown */}
            {components && (
              <div className="p-4 rounded-lg bg-white/[0.02] border border-athena-border">
                <div className="flex items-center justify-between mb-3">
                  <h4 className="text-[11px] font-mono text-athena-muted uppercase tracking-wider">Score Breakdown</h4>
                  <span className="font-mono font-bold text-[13px] text-athena-text">{score.toFixed(1)} / 5.0</span>
                </div>
                <div className="space-y-2.5">
                  <ScoreBar label={`Thesis (${Math.round((components.thesis?.weight || 0) * 100)}%)`} score={components.thesis?.score ?? 0} max={components.thesis?.max || 5} />
                  <ScoreBar label={`Team (${Math.round((components.team?.weight || 0) * 100)}%)`} score={components.team?.score} max={components.team?.max || 5} />
                  <ScoreBar label={`Program (${Math.round((components.program?.weight || 0) * 100)}%)`} score={components.program?.score ?? 0} max={components.program?.max || 5} />
                  <ScoreBar label={`Traction (${Math.round((components.traction?.weight || 0) * 100)}%)`} score={components.traction?.score ?? 0} max={components.traction?.max || 5} />
                  <ScoreBar label={`Data (${Math.round((components.data?.weight || 0) * 100)}%)`} score={components.data?.score ?? 0} max={components.data?.max || 5} />
                </div>
              </div>
            )}

            <div className="space-y-4">
              {/* Founders */}
              {founders.length > 0 && (
                <div className="p-4 rounded-lg bg-white/[0.02] border border-athena-border">
                  <h4 className="text-[11px] font-mono text-athena-muted uppercase tracking-wider mb-3">Founders</h4>
                  <div className="space-y-2">
                    {founders.map((f, i) => (
                      <div key={i} className="flex items-center justify-between gap-2">
                        <div className="min-w-0">
                          <span className="text-[12px] text-athena-text font-medium">{f.name}</span>
                          {f.title && <span className="text-[11px] text-athena-muted/60 ml-2">{f.title}</span>}
                        </div>
                        {f.linkedin_url && (
                          <a href={f.linkedin_url} target="_blank" rel="noopener noreferrer"
                             className="flex-shrink-0 text-[11px] text-sky-400 hover:text-sky-300"
                             onClick={(e) => e.stopPropagation()}>
                            LinkedIn
                          </a>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Programs */}
              {company.programs && company.programs.length > 0 && (
                <div className="p-4 rounded-lg bg-white/[0.02] border border-athena-border">
                  <h4 className="text-[11px] font-mono text-athena-muted uppercase tracking-wider mb-3">Programs</h4>
                  <div className="space-y-1.5">
                    {company.programs.map((p, i) => (
                      <div key={i} className="text-[12px]">
                        <span className="text-athena-text font-medium">{p.program_name}</span>
                        {p.cohort && <span className="font-mono text-athena-muted/50 ml-2">{p.cohort}</span>}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Signals */}
          {company.signals && company.signals.length > 0 && (
            <div>
              <h4 className="text-[11px] font-mono text-athena-muted uppercase tracking-wider mb-2">Signals</h4>
              {company.signals.map((s, i) => {
                const meta = s.metadata || {};
                const pts = s.source_name === 'HackerNews' && meta.points ? ` · ${meta.points}pts` : '';
                const date = meta.published || meta.posted_at?.split('T')[0] || s.detected_at?.split(' ')[0] || '';
                return (
                  <div key={i} className="flex items-center gap-3 text-[12px] py-1.5 border-b border-athena-border/50 last:border-0">
                    <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                      s.signal_layer === 'curated' ? 'bg-emerald-400' : 'bg-sky-400'
                    }`} />
                    <span className="font-medium text-athena-text w-28 flex-shrink-0">{s.source_name}</span>
                    <span className="text-athena-muted/60">{pts}</span>
                    {s.source_url && (
                      <a href={s.source_url} target="_blank" rel="noopener noreferrer"
                         className="text-athena-accent hover:text-athena-accent-hover text-[11px]"
                         onClick={(e) => e.stopPropagation()}>
                        link ↗
                      </a>
                    )}
                    <span className="ml-auto font-mono text-[11px] text-athena-muted/40">{date}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function Top20() {
  const [companies, setCompanies] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchStats().then(setStats).catch(() => {});
    fetchTop20()
      .then((data) => {
        setCompanies(data.results || []);
      })
      .catch(() => setError('Could not load data'))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="min-h-screen bg-athena-bg pt-14">
      <TopBar stats={stats} />

      <div className="max-w-[1440px] mx-auto px-6 pt-8 pb-4">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="font-sans font-bold text-[22px] text-athena-text">
              Top 20 Companies
            </h1>
            <p className="text-[13px] text-athena-muted mt-1">
              Highest-scoring curated companies by Athena Score
            </p>
          </div>
          <Link
            to="/"
            className="px-4 py-2 rounded-lg bg-athena-card border border-athena-border text-[13px] text-athena-muted
                       hover:text-athena-accent hover:border-athena-accent/30 font-sans transition-colors no-underline"
          >
            ← Back to Dashboard
          </Link>
        </div>

        {loading ? (
          <div className="space-y-3">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="h-20 rounded-xl bg-athena-card border border-athena-border animate-pulse" />
            ))}
          </div>
        ) : error ? (
          <div className="flex flex-col items-center justify-center py-24 text-athena-muted">
            <p className="font-sans text-sm">{error}</p>
          </div>
        ) : (
          <div className="space-y-2.5">
            {companies.map((c, i) => (
              <RankCard key={c.id} company={c} rank={i + 1} />
            ))}
          </div>
        )}
      </div>

      <Footer />
    </div>
  );
}
