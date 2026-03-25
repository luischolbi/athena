import { useState } from 'react';
import { createFounder, updateFounder, deleteFounder } from '../api';

function ScoreBadge({ score, isCrossLayer, priority }) {
  const s = score != null ? score : 0;
  let bg, text, glow;
  if (s >= 4.0) {
    bg = 'bg-emerald-500/20';
    text = 'text-emerald-400';
    glow = 'shadow-[0_0_14px_rgba(52,211,153,0.25)]';
  } else if (s >= 3.5) {
    bg = 'bg-blue-500/20';
    text = 'text-blue-400';
    glow = '';
  } else if (s >= 3.0) {
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
        <span className={`font-mono font-bold text-[15px] leading-none ${text}`}>{s.toFixed(1)}</span>
      </div>
      {isCrossLayer && (
        <span className="text-[10px] text-athena-accent" title="Cross-layer match">✦</span>
      )}
    </div>
  );
}

function PriorityBadge({ priority }) {
  if (priority === 'high') {
    return (
      <span className="inline-flex items-center px-1.5 py-0 rounded text-[9px] font-mono text-emerald-400/80 border border-emerald-500/20 leading-relaxed">
        High Priority
      </span>
    );
  }
  if (priority === 'investigate') {
    return (
      <span className="inline-flex items-center px-1.5 py-0 rounded text-[9px] font-mono text-blue-400/80 border border-blue-500/20 leading-relaxed">
        Investigate
      </span>
    );
  }
  if (priority === 'radar') {
    return (
      <span className="inline-flex items-center px-1.5 py-0 rounded text-[9px] font-mono text-athena-muted/60 border border-athena-muted/20 leading-relaxed">
        On Radar
      </span>
    );
  }
  return null;
}

function TierBadge({ tier }) {
  if (tier === 1) {
    return (
      <span className="inline-flex items-center px-1.5 py-0 rounded text-[9px] font-mono text-emerald-400/80 border border-emerald-500/20 leading-relaxed" title="Tier 1 — Curated data">
        Curated
      </span>
    );
  }
  if (tier === 2) {
    return (
      <span className="inline-flex items-center px-1.5 py-0 rounded text-[9px] font-mono text-blue-400/80 border border-blue-500/20 leading-relaxed" title="Tier 2 — Standard data">
        Standard
      </span>
    );
  }
  if (tier === 3) {
    return (
      <span className="inline-flex items-center px-1.5 py-0 rounded text-[9px] font-mono text-athena-muted/50 border border-athena-muted/15 leading-relaxed" title="Tier 3 — Discovery data">
        Discovery
      </span>
    );
  }
  return null;
}

function StagePill({ stage, stageDisplay, stageConfidence }) {
  const styles = {
    'Pre-money':  'border-athena-muted/30 text-athena-muted',
    'Grant only': 'border-blue-500/30 text-blue-400',
    'Pre-seed':   'border-emerald-500/30 text-emerald-400',
    'Seed':       'border-green-500/30 text-green-400',
    'Series A':   'border-purple-500/30 text-purple-400',
    'Unknown':    'border-dashed border-athena-muted/20 text-athena-muted/50',
  };
  const isOutdated = stageConfidence === 'outdated';
  const isStale = stageConfidence === 'stale';
  const lookupStage = isOutdated ? 'Unknown' : stage;
  const cls = styles[lookupStage] || styles['Unknown'];
  const opacity = isStale ? 'opacity-60' : '';
  return (
    <span className={`px-2 py-0.5 rounded text-[10px] font-mono font-medium border ${cls} ${opacity}`}>
      {stageDisplay || stage || 'Unknown'}
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
  if (company.programs && company.programs.length > 0) {
    const p = company.programs[0];
    if (p.cohort) {
      if (p.cohort.startsWith('Stage')) {
        return `${p.cohort}`;
      }
      return `${p.cohort} cohort`;
    }
  }

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
      className="card-enter"
      style={{ animationDelay: `${delay}ms` }}
    >
      <div
        onClick={() => onClick(company)}
        className={`flex items-start gap-4 p-4 rounded-xl cursor-pointer border transition-all duration-200
          ${isExpanded
            ? 'bg-athena-card-hover border-athena-accent/30 shadow-[0_0_20px_rgba(59,130,246,0.06)]'
            : 'bg-athena-card border-athena-border hover:border-athena-border-hover hover:bg-athena-card-hover'
          }`}
      >
        {/* Score */}
        <div className="flex-shrink-0 pt-0.5">
          <ScoreBadge score={company.athena_score} isCrossLayer={company.is_cross_layer} priority={company.priority} />
        </div>

        {/* Main content */}
        <div className="flex-1 min-w-0">
          <h3 className="font-sans font-semibold text-athena-text text-[15px] truncate flex items-center gap-1.5">
            {company.name}
            <PriorityBadge priority={company.priority} />
            <TierBadge tier={company.data_tier} />
            {company.cohort ? (
              <span className="inline-flex items-center px-1.5 py-0 rounded text-[9px] font-mono text-violet-400/80 border border-violet-500/20 leading-relaxed">
                {company.cohort}
              </span>
            ) : company.programs && company.programs.length > 0 ? (
              <span className="inline-flex items-center px-1.5 py-0 rounded text-[9px] font-mono text-athena-muted/50 border border-athena-muted/15 leading-relaxed">
                Year unknown
              </span>
            ) : null}
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
            {company.company_status === 'inactive' && (
              <span className="px-1.5 py-0.5 rounded text-[10px] text-orange-400/70 border border-orange-500/15">
                Website inactive
              </span>
            )}
            {company.company_status === 'redirect' && (
              <span className="px-1.5 py-0.5 rounded text-[10px] text-amber-400/70 border border-amber-500/15">
                Domain changed
              </span>
            )}
            {company.freshness_score != null && company.freshness_score <= 2 && (
              <span className="px-1.5 py-0.5 rounded text-[10px] text-athena-muted/50 border border-athena-muted/15">
                Data may be outdated
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
            <StagePill stage={company.stage} stageDisplay={company.stage_display} stageConfidence={company.stage_confidence} />
          </div>
        </div>

        {/* Signal badges — right side */}
        <div className="flex-shrink-0 flex flex-col items-end gap-1.5">
          {company.source_count > 0 && (
            <span className="text-[10px] text-athena-muted/60 font-mono mb-0.5">
              {company.source_count} {company.source_count === 1 ? 'source' : 'sources'}
            </span>
          )}
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


function ScoreBar({ label, score, max, detail, tooltip, overridden }) {
  const [expanded, setExpanded] = useState(false);
  const isNull = score == null;
  const s = isNull ? 0 : score;
  const pct = max > 0 ? (s / max) * 100 : 0;
  let barColor;
  if (isNull) barColor = 'bg-transparent';
  else if (pct >= 75) barColor = 'bg-athena-accent';
  else if (pct >= 50) barColor = 'bg-amber-400';
  else if (pct > 0) barColor = 'bg-athena-muted/40';
  else barColor = 'bg-transparent';

  function handleClick(e) {
    e.stopPropagation();
    e.preventDefault();
    setExpanded(prev => !prev);
  }

  return (
    <div
      className={`space-y-1 ${tooltip ? 'cursor-pointer' : ''}`}
      onClick={tooltip ? handleClick : (e) => e.stopPropagation()}
    >
      <div className="flex items-center justify-between">
        <span className="text-[12px] font-medium text-athena-text">
          {label}
          {tooltip && <span className="ml-1 text-[10px] text-athena-muted/40">{expanded ? '▾' : '▸'}</span>}
        </span>
        <span className="font-mono text-[12px] text-athena-muted">
          {isNull ? '—' : `${s}/${max}`}
          {overridden && <span className="ml-1 text-[10px] text-amber-400/60">(overridden)</span>}
        </span>
      </div>
      <div className="h-1.5 rounded-full bg-white/5 overflow-hidden">
        {isNull ? (
          <div className="h-full w-full bg-athena-muted/10" style={{ backgroundImage: 'repeating-linear-gradient(90deg, transparent, transparent 4px, rgba(255,255,255,0.03) 4px, rgba(255,255,255,0.03) 8px)' }} />
        ) : (
          <div
            className={`h-full rounded-full ${barColor} transition-all duration-500`}
            style={{ width: `${pct}%` }}
          />
        )}
      </div>
      {tooltip ? (
        expanded ? (
          <p className="text-[11px] text-athena-accent/70 leading-snug">{tooltip}</p>
        ) : (
          <p className="text-[11px] text-athena-muted/30 truncate">Click to see reasoning</p>
        )
      ) : (
        <p className="text-[11px] text-athena-muted/50 truncate">{detail}</p>
      )}
    </div>
  );
}

function WebsiteStatus({ status }) {
  if (status === 'active') {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-mono text-emerald-400/70">
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
        Active
      </span>
    );
  }
  if (status === 'inactive') {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-mono text-orange-400/70">
        <span className="w-1.5 h-1.5 rounded-full bg-orange-400" />
        Inactive
      </span>
    );
  }
  if (status === 'redirect') {
    return (
      <span className="inline-flex items-center gap-1 text-[10px] font-mono text-amber-400/70">
        <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
        Redirect
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-[10px] font-mono text-athena-muted/50">
      <span className="w-1.5 h-1.5 rounded-full bg-athena-muted/40" />
      Unknown
    </span>
  );
}

function FounderForm({ initial, onSave, onCancel, saving }) {
  const [name, setName] = useState(initial?.name || '');
  const [title, setTitle] = useState(initial?.title || '');
  const [linkedinUrl, setLinkedinUrl] = useState(initial?.linkedin_url || '');
  const [email, setEmail] = useState(initial?.email || '');

  function handleSubmit(e) {
    e.preventDefault();
    e.stopPropagation();
    if (!name.trim() || !title.trim()) return;
    onSave({ name: name.trim(), title: title.trim(), linkedin_url: linkedinUrl.trim() || null, email: email.trim() || null });
  }

  const inputCls = "w-full h-7 px-2.5 rounded bg-athena-bg border border-athena-border text-[12px] text-athena-text placeholder:text-athena-muted/40 focus:outline-none focus:border-athena-accent/40 font-sans";

  return (
    <form onSubmit={handleSubmit} onClick={(e) => e.stopPropagation()} className="space-y-2 mt-2">
      <div className="grid grid-cols-2 gap-2">
        <input type="text" placeholder="Name *" value={name} onChange={(e) => setName(e.target.value)} className={inputCls} required />
        <input type="text" placeholder="Title/Role *" value={title} onChange={(e) => setTitle(e.target.value)} className={inputCls} required />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <input type="url" placeholder="LinkedIn URL" value={linkedinUrl} onChange={(e) => setLinkedinUrl(e.target.value)} className={inputCls} />
        <input type="email" placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} className={inputCls} />
      </div>
      <div className="flex items-center gap-2 pt-1">
        <button type="submit" disabled={saving || !name.trim() || !title.trim()}
          className="h-7 px-3 rounded text-[11px] font-medium bg-athena-accent/15 text-athena-accent border border-athena-accent/30 hover:bg-athena-accent/25 disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
          {saving ? 'Saving...' : (initial ? 'Update' : 'Add Founder')}
        </button>
        <button type="button" onClick={(e) => { e.stopPropagation(); onCancel(); }}
          className="h-7 px-3 rounded text-[11px] font-medium text-athena-muted hover:text-athena-text transition-colors">
          Cancel
        </button>
      </div>
    </form>
  );
}

function CompanyDetail({ company }) {
  const bd = company.score_breakdown || { total: company.athena_score || 0, reasons: [], components: null };
  const components = bd.components;

  const thesisOverride = company.thesis_override != null;
  const thesisTooltip = thesisOverride
    ? `${company.thesis_reasoning || ''}${company.thesis_reasoning_original ? `\n\nOriginal LLM assessment: ${company.thesis_reasoning_original}` : ''}`
    : company.thesis_reasoning || null;

  const teamTooltip = company.team_metrics ? (<>
    <span className="font-mono">Tech: {company.team_metrics.technical_excellence} | Builder: {company.team_metrics.builder_track_record} | Domain: {company.team_metrics.domain_expertise} | Comp: {company.team_metrics.complementarity}</span>
    {company.team_reasoning && <><br />{company.team_reasoning.split('.')[0]}.</>}
  </>) : null;

  const [founders, setFounders] = useState(company.founders || []);
  const [showAddForm, setShowAddForm] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [saving, setSaving] = useState(false);

  async function handleAddFounder(data) {
    setSaving(true);
    try {
      const created = await createFounder(company.id, data);
      setFounders((prev) => [...prev, created]);
      setShowAddForm(false);
    } catch (err) {
      console.error('Failed to add founder:', err);
    } finally {
      setSaving(false);
    }
  }

  async function handleUpdateFounder(founderId, data) {
    setSaving(true);
    try {
      const updated = await updateFounder(founderId, data);
      setFounders((prev) => prev.map((f) => f.id === founderId ? updated : f));
      setEditingId(null);
    } catch (err) {
      console.error('Failed to update founder:', err);
    } finally {
      setSaving(false);
    }
  }

  async function handleDeleteFounder(founderId) {
    try {
      await deleteFounder(founderId);
      setFounders((prev) => prev.filter((f) => f.id !== founderId));
    } catch (err) {
      console.error('Failed to delete founder:', err);
    }
  }

  return (
    <div className="mt-1 p-5 rounded-xl bg-athena-card border border-athena-border space-y-5 animate-fade-in">
      {/* Header row: tier + stage context */}
      <div className="flex items-center gap-3 flex-wrap">
        <TierBadge tier={company.data_tier} />
        {company.stage && (
          <StagePill stage={company.stage} stageDisplay={company.stage_display} stageConfidence={company.stage_confidence} />
        )}
        {company.programs && company.programs.length > 0 && (
          <span className="text-[11px] text-athena-muted/60">
            {company.programs.map(p => `${p.program_name}${p.cohort ? ` ${p.cohort}` : ''}`).join(' · ')}
          </span>
        )}
      </div>

      {/* Description */}
      {company.description && (
        <p className="text-[13px] text-athena-muted leading-relaxed">
          {company.description}
        </p>
      )}

      {/* Website with status */}
      {company.website && (
        <div className="flex items-center gap-3">
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
          <WebsiteStatus status={company.company_status} />
        </div>
      )}

      <div className="grid md:grid-cols-2 gap-4">
        {/* Score breakdown */}
        <div className="p-4 rounded-lg bg-white/[0.02] border border-athena-border">
          <div className="flex items-center justify-between mb-4">
            <h4 className="text-[11px] font-mono font-medium text-athena-muted uppercase tracking-wider">
              Score Breakdown
            </h4>
            <span className="font-mono font-bold text-[14px] text-athena-text">{typeof bd.total === 'number' ? bd.total.toFixed(1) : bd.total} / 5.0</span>
          </div>
          {components ? (
            <div className="space-y-3">
              <ScoreBar label={`Thesis Fit (${Math.round((components.thesis?.weight || 0) * 100)}%)`} score={components.thesis?.score ?? 0} max={components.thesis?.max || 5} detail={components.thesis?.label} tooltip={thesisTooltip} overridden={thesisOverride} />
              <ScoreBar label={`Team Signal (${Math.round((components.team?.weight || 0) * 100)}%)`} score={components.team?.score} max={components.team?.max || 5} detail={components.team?.label} tooltip={teamTooltip} />
              <ScoreBar label={`Program (${Math.round((components.program?.weight || 0) * 100)}%)`} score={components.program?.score ?? 0} max={components.program?.max || 5} detail={components.program?.label} />
              <ScoreBar label={`Traction (${Math.round((components.traction?.weight || 0) * 100)}%)`} score={components.traction?.score ?? 0} max={components.traction?.max || 5} detail={components.traction?.label} />
              <ScoreBar label={`Data (${Math.round((components.data?.weight || 0) * 100)}%)`} score={components.data?.score ?? 0} max={components.data?.max || 5} detail={components.data?.label} />
            </div>
          ) : (
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

        {/* Right column: Founders + Programs */}
        <div className="space-y-4">
          {/* Founders */}
          <div className="p-4 rounded-lg bg-white/[0.02] border border-athena-border">
            <h4 className="text-[11px] font-mono font-medium text-athena-muted uppercase tracking-wider mb-3">
              Founders
            </h4>
            {founders.length > 0 ? (
              <div className="space-y-2">
                {founders.map((f) => (
                  <div key={f.id}>
                    {editingId === f.id ? (
                      <FounderForm
                        initial={f}
                        saving={saving}
                        onSave={(data) => handleUpdateFounder(f.id, data)}
                        onCancel={() => setEditingId(null)}
                      />
                    ) : (
                      <div className="flex items-center gap-2 group">
                        <div className="flex-1 min-w-0">
                          <span className="text-[12px] text-athena-text font-medium">{f.name}</span>
                          {f.title && (
                            <span className="text-[11px] text-athena-muted/60 ml-2">{f.title}</span>
                          )}
                        </div>
                        {f.linkedin_url && (
                          <a
                            href={f.linkedin_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="flex-shrink-0 text-[11px] text-sky-400 hover:text-sky-300 font-medium"
                            onClick={(e) => e.stopPropagation()}
                          >
                            LinkedIn
                          </a>
                        )}
                        <div className="flex-shrink-0 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                          <button
                            onClick={(e) => { e.stopPropagation(); setEditingId(f.id); setShowAddForm(false); }}
                            className="w-5 h-5 rounded flex items-center justify-center text-athena-muted/50 hover:text-athena-accent hover:bg-athena-accent/10 transition-colors"
                            title="Edit"
                          >
                            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                            </svg>
                          </button>
                          <button
                            onClick={(e) => { e.stopPropagation(); handleDeleteFounder(f.id); }}
                            className="w-5 h-5 rounded flex items-center justify-center text-athena-muted/50 hover:text-red-400 hover:bg-red-400/10 transition-colors"
                            title="Delete"
                          >
                            <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                            </svg>
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-[11px] text-athena-muted/40">No founder data</p>
            )}

            {/* Add founder form / button */}
            {showAddForm ? (
              <FounderForm saving={saving} onSave={handleAddFounder} onCancel={() => setShowAddForm(false)} />
            ) : (
              <button
                onClick={(e) => { e.stopPropagation(); setShowAddForm(true); setEditingId(null); }}
                className="mt-3 inline-flex items-center gap-1.5 h-7 px-2.5 rounded text-[11px] font-medium text-athena-muted hover:text-athena-accent hover:bg-athena-accent/10 border border-athena-border hover:border-athena-accent/30 transition-colors"
              >
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
                </svg>
                Add Founder
              </button>
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
      </div>

      {/* Signal timeline */}
      {company.signals && company.signals.length > 0 && (
        <div>
          <h4 className="text-[11px] font-mono font-medium text-athena-muted uppercase tracking-wider mb-3">
            Signals
          </h4>
          <div className="space-y-0">
            {company.signals.map((s, i) => {
              const meta = s.metadata || {};
              const pts = s.source_name === 'HackerNews' && meta.points ? ` · ${meta.points}pts` : '';
              const date = meta.published || meta.posted_at?.split('T')[0] || s.detected_at?.split(' ')[0] || '';
              return (
                <div key={i} className="flex items-center gap-3 text-[12px] py-2 border-b border-athena-border/50 last:border-0">
                  <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                    s.signal_layer === 'curated' ? 'bg-emerald-400' : 'bg-sky-400'
                  }`} />
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
