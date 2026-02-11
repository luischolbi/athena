function ScoreBadge({ score, isCrossLayer, rising }) {
  let bg, text, glow;
  if (score >= 8) {
    bg = 'bg-blue-500/20';
    text = 'text-blue-400';
    glow = 'shadow-[0_0_14px_rgba(59,130,246,0.25)]';
  } else if (score >= 6) {
    bg = 'bg-athena-accent/12';
    text = 'text-athena-accent';
    glow = '';
  } else if (score >= 4) {
    bg = 'bg-amber-500/10';
    text = 'text-amber-400';
    glow = '';
  } else {
    bg = 'bg-white/5';
    text = 'text-athena-muted';
    glow = '';
  }

  return (
    <div className="flex flex-col items-center gap-0.5">
      <div className={`relative w-11 h-11 rounded-xl flex items-center justify-center ${bg} ${glow}`}>
        <span className={`font-mono font-bold text-[15px] leading-none ${text}`}>{score}</span>
        <span className={`font-mono text-[10px] leading-none ${text} opacity-40 ml-px`}>/10</span>
        {rising && (
          <span className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-emerald-500/20 flex items-center justify-center" title="Rising — score +2 since last run">
            <svg className="w-2.5 h-2.5 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M7 17L17 7M17 7H7M17 7V17" />
            </svg>
          </span>
        )}
      </div>
      {isCrossLayer && (
        <span className="text-[10px] text-athena-accent" title="Cross-layer match">✦</span>
      )}
    </div>
  );
}

function StagePill({ stage }) {
  const styles = {
    'Pre-money':  'border-athena-muted/30 text-athena-muted',
    'Grant only': 'border-blue-500/30 text-blue-400',
    'Pre-seed':   'border-emerald-500/30 text-emerald-400',
    'Seed':       'border-green-500/30 text-green-400',
    'Series A':   'border-purple-500/30 text-purple-400',
    'Unknown':    'border-dashed border-athena-muted/20 text-athena-muted/50',
  };
  const cls = styles[stage] || styles['Unknown'];
  return (
    <span className={`px-2 py-0.5 rounded text-[10px] font-mono font-medium border ${cls}`}>
      {stage || 'Unknown'}
    </span>
  );
}

function SignalBadge({ signal }) {
  const name = signal.source_name;
  const meta = signal.metadata || {};

  if (signal.signal_layer === 'curated') {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium bg-white/5 text-athena-muted border border-athena-border">
        {name}
      </span>
    );
  }

  if (name === 'HackerNews') {
    const pts = meta.points || 0;
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium bg-orange-500/10 text-orange-400 border border-orange-500/20">
        HN{pts > 0 ? ` · ${pts}pts` : ''}
      </span>
    );
  }

  if (name === 'ProductHunt') {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium bg-red-500/10 text-red-400 border border-red-500/20">
        ProductHunt
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium bg-sky-500/10 text-sky-400 border border-sky-500/20">
      {name}
    </span>
  );
}

/** Extract the best date to display for a company */
function getDisplayDate(company) {
  // 1. Cohort/year from programs
  if (company.programs && company.programs.length > 0) {
    const p = company.programs[0];
    if (p.cohort) {
      // VK stages: "Stage 2" — also show program context
      if (p.cohort.startsWith('Stage')) {
        return `${p.cohort}`;
      }
      // Year cohort
      return `${p.cohort} cohort`;
    }
  }

  // 2. Published date from realtime signals
  for (const s of company.signals || []) {
    const meta = s.metadata || {};
    if (meta.published) return meta.published;
    if (meta.posted_at) {
      return meta.posted_at.split('T')[0];
    }
  }

  return null;
}

export default function CompanyCard({ company, isExpanded, onClick, delay }) {
  const displayDate = getDisplayDate(company);

  // Dedupe signals by source for badge display
  const seenSources = new Set();
  const badges = [];
  for (const s of company.signals || []) {
    if (seenSources.has(s.source_name)) {
      // For HN, keep the one with most points
      if (s.source_name === 'HackerNews') {
        const existing = badges.find(b => b.source_name === 'HackerNews');
        if (existing && (s.metadata?.points || 0) > (existing.metadata?.points || 0)) {
          badges[badges.indexOf(existing)] = s;
        }
      }
      continue;
    }
    seenSources.add(s.source_name);
    badges.push(s);
  }

  return (
    <div
      onClick={() => onClick(company)}
      className="card-enter"
      style={{ animationDelay: `${delay}ms` }}
    >
      <div
        className={`flex items-start gap-4 p-4 rounded-xl cursor-pointer border transition-all duration-200
          ${isExpanded
            ? 'bg-athena-card-hover border-athena-accent/30 shadow-[0_0_20px_rgba(59,130,246,0.06)]'
            : 'bg-athena-card border-athena-border hover:border-athena-border-hover hover:bg-athena-card-hover'
          }`}
      >
        {/* Score */}
        <div className="flex-shrink-0 pt-0.5">
          <ScoreBadge score={company.heat_score} isCrossLayer={company.is_cross_layer} rising={company.rising} />
        </div>

        {/* Main content */}
        <div className="flex-1 min-w-0">
          <h3 className="font-sans font-semibold text-athena-text text-[15px] truncate">
            {company.name}
          </h3>
          {company.description && (
            <p className="text-[13px] text-athena-muted mt-0.5 line-clamp-1">
              {company.description}
            </p>
          )}

          {/* Tags row */}
          <div className="flex items-center gap-2.5 flex-wrap mt-2.5 text-[11px] text-athena-muted">
            {company.sector && (
              <span className="px-2 py-0.5 rounded border border-athena-border text-athena-muted">
                {company.sector}
              </span>
            )}
            {company.geography && (
              <span className="flex items-center gap-1">
                <svg className="w-3 h-3 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
                {company.city ? `${company.city}` : company.geography}
              </span>
            )}
            {displayDate && (
              <span className="font-mono text-athena-muted/60">{displayDate}</span>
            )}
            <StagePill stage={company.stage} />
          </div>
        </div>

        {/* Signal badges — right side */}
        <div className="flex-shrink-0 flex flex-col items-end gap-1.5">
          {badges.slice(0, 3).map((s, i) => (
            <SignalBadge key={i} signal={s} />
          ))}
          {badges.length > 3 && (
            <span className="text-[10px] text-athena-muted font-mono">+{badges.length - 3}</span>
          )}
        </div>
      </div>

      {/* Expanded detail — inline accordion */}
      {isExpanded && <CompanyDetail company={company} />}
    </div>
  );
}


function ScoreBar({ label, score, max, detail }) {
  const pct = max > 0 ? (score / max) * 100 : 0;
  let barColor;
  if (pct >= 75) barColor = 'bg-athena-accent';
  else if (pct >= 50) barColor = 'bg-amber-400';
  else if (pct > 0) barColor = 'bg-athena-muted/40';
  else barColor = 'bg-transparent';

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <span className="text-[12px] font-medium text-athena-text">{label}</span>
        <span className="font-mono text-[12px] text-athena-muted">{score}/{max}</span>
      </div>
      <div className="h-1.5 rounded-full bg-white/5 overflow-hidden">
        <div
          className={`h-full rounded-full ${barColor} transition-all duration-500`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="text-[11px] text-athena-muted/50 truncate">{detail}</p>
    </div>
  );
}

function CompanyDetail({ company }) {
  const bd = company.score_breakdown || { total: company.heat_score, reasons: [], components: null };
  const components = bd.components;

  return (
    <div className="mt-1 p-5 rounded-xl bg-athena-card border border-athena-border space-y-5 animate-fade-in">
      {/* Description */}
      {company.description && (
        <p className="text-[13px] text-athena-muted leading-relaxed">
          {company.description}
        </p>
      )}

      {/* Website */}
      {company.website && (
        <a
          href={company.website}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 text-[13px] text-athena-accent hover:text-athena-accent-hover font-medium"
          onClick={(e) => e.stopPropagation()}
        >
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
          </svg>
          {company.website.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '')}
        </a>
      )}

      <div className="grid md:grid-cols-2 gap-5">
        {/* Score breakdown — component bars */}
        <div className="p-4 rounded-lg bg-white/[0.02] border border-athena-border">
          <div className="flex items-center justify-between mb-4">
            <h4 className="text-[11px] font-mono font-medium text-athena-muted uppercase tracking-wider">
              Score Breakdown
            </h4>
            <span className="font-mono font-bold text-[14px] text-athena-text">{bd.total}/10</span>
          </div>
          {components ? (
            <div className="space-y-3">
              <ScoreBar label="Program" score={components.program.score} max={components.program.max} detail={components.program.label} />
              <ScoreBar label="Buzz" score={components.buzz.score} max={components.buzz.max} detail={components.buzz.label} />
              <ScoreBar label="Sources" score={components.sources.score} max={components.sources.max} detail={components.sources.label} />
              <ScoreBar label="Recency" score={components.recency.score} max={components.recency.max} detail={components.recency.label} />
            </div>
          ) : (
            /* Fallback: flat reason list for older cached data */
            bd.reasons.length > 0 ? (
              <ul className="space-y-1.5">
                {bd.reasons.map((r, i) => (
                  <li key={i} className="text-[12px] text-athena-muted flex items-start gap-2">
                    <span className="text-athena-accent mt-px">→</span>
                    <span>{r}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-[12px] text-athena-muted/50">Base score — no additional signals.</p>
            )
          )}
        </div>

        {/* Programs */}
        {company.programs && company.programs.length > 0 && (
          <div className="p-4 rounded-lg bg-white/[0.02] border border-athena-border">
            <h4 className="text-[11px] font-mono font-medium text-athena-muted uppercase tracking-wider mb-3">
              Programs
            </h4>
            <div className="space-y-2">
              {company.programs.map((p, i) => (
                <div key={i} className="text-[12px] text-athena-muted">
                  <span className="text-athena-text font-medium">{p.program_name}</span>
                  {p.program_type && <span className="text-athena-muted/60"> · {p.program_type}</span>}
                  {p.cohort && <span className="font-mono text-athena-muted/50 ml-2">{p.cohort}</span>}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Signal timeline */}
      {company.signals && company.signals.length > 0 && (
        <div>
          <h4 className="text-[11px] font-mono font-medium text-athena-muted uppercase tracking-wider mb-3">
            Signals
          </h4>
          <div className="space-y-1">
            {company.signals.map((s, i) => {
              const meta = s.metadata || {};
              const pts = s.source_name === 'HackerNews' && meta.points ? ` · ${meta.points}pts` : '';
              const date = meta.published || meta.posted_at?.split('T')[0] || s.detected_at?.split(' ')[0] || '';
              return (
                <div key={i} className="flex items-center gap-3 text-[12px] py-1.5 border-b border-athena-border/50 last:border-0">
                  <span className="font-medium text-athena-text w-32 flex-shrink-0">{s.source_name}</span>
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
        </div>
      )}
    </div>
  );
}
