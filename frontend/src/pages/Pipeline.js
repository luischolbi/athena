import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import TopBar from '../components/TopBar';
import { fetchPipeline, fetchStats, moveInPipeline, addPipelineNote, removeFromPipeline, quickScreen } from '../api';
import { showToast } from '../components/Toast';

const COLUMNS = [
  { key: 'new', label: 'New', color: '#3b82f6', desc: 'Auto-populated from Athena scores \u2265 4.0' },
  { key: 'reviewing', label: 'Reviewing', color: '#f59e0b', desc: 'Scout is currently evaluating' },
  { key: 'shortlisted', label: 'Shortlisted', color: '#4ade80', desc: 'Strong fit \u2014 ready for submission' },
  { key: 'submitted', label: 'Submitted', color: '#8b5cf6', desc: 'Sent to Airtable pipeline' },
  { key: 'passed', label: 'Passed', color: '#6b7280', desc: 'Reviewed and declined' },
];

const PIPELINE_CSS = `
.pl-stats-bar { padding: 10px 28px; display: flex; gap: 20px; align-items: center; border-bottom: 1px solid rgba(255,255,255,0.04); font-size: 12px; color: #8b8b9e; flex-wrap: wrap; font-family: 'Inter', system-ui, sans-serif; }
.pl-stats-bar strong { color: #e6e6e6; font-weight: 600; }
.pl-stat-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 5px; vertical-align: middle; }
.pl-stats-sep { color: rgba(255,255,255,0.12); }
.pl-stats-right { margin-left: auto; font-size: 11px; color: #6b6b7e; }

.pl-board { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; padding: 16px 20px; min-height: calc(100vh - 110px); }
.pl-column { padding: 0 2px; display: flex; flex-direction: column; }
.pl-col-header { display: flex; align-items: center; gap: 6px; margin-bottom: 12px; padding-bottom: 10px; flex-wrap: wrap; }
.pl-col-label { font-size: 13px; font-weight: 700; color: #e6e6e6; letter-spacing: -0.01em; font-family: 'Inter', system-ui, sans-serif; }
.pl-col-count { font-size: 11px; padding: 1px 9px; border-radius: 10px; font-weight: 700; font-family: 'JetBrains Mono', monospace; }
.pl-col-desc { font-size: 9px; color: #6b6b7e; margin-left: auto; font-style: italic; width: 100%; margin-top: 2px; font-family: 'Inter', system-ui, sans-serif; }
.pl-col-cards { display: flex; flex-direction: column; gap: 8px; min-height: 80px; padding: 4px; border-radius: 8px; transition: background 0.2s; flex: 1; }
.pl-col-cards.drag-over { background: rgba(59,130,246,0.06); }

.pl-card { background: #161b22; border: 1px solid rgba(255,255,255,0.06); border-radius: 10px; padding: 10px 12px; cursor: grab; transition: all 0.15s; position: relative; font-family: 'Inter', system-ui, sans-serif; }
.pl-card:hover { border-color: rgba(59,130,246,0.3); }
.pl-card:active { cursor: grabbing; }
.pl-card.dragging { opacity: 0.35; transform: rotate(1.5deg); }
.pl-card.shortlisted { border-color: rgba(74,222,128,0.25); }
.pl-card.submitted { border-color: rgba(139,92,246,0.3); }
.pl-card.passed { opacity: 0.45; }

.pl-card-top { display: flex; justify-content: space-between; align-items: start; }
.pl-card-name { font-size: 12px; font-weight: 600; color: #e6e6e6; line-height: 1.3; }
.pl-card-score { font-size: 11px; font-weight: 700; font-family: 'JetBrains Mono', monospace; flex-shrink: 0; margin-left: 6px; }

.pl-card-desc { font-size: 10.5px; color: #8b8b9e; margin: 4px 0 6px; line-height: 1.45; display: -webkit-box; -webkit-box-orient: vertical; overflow: hidden; }
.pl-card-desc.clamp { -webkit-line-clamp: 2; }

.pl-tags { display: flex; gap: 3px; flex-wrap: wrap; }
.pl-tag { font-size: 9px; padding: 2px 6px; border-radius: 4px; background: rgba(255,255,255,0.04); color: #8b8b9e; font-weight: 500; }
.pl-tag.program { background: rgba(59,130,246,0.1); color: #60a5fa; }

.pl-breakdown { display: flex; gap: 10px; margin: 8px 0 4px; padding-top: 8px; border-top: 1px solid rgba(255,255,255,0.05); }
.pl-breakdown-item { text-align: center; }
.pl-breakdown-label { font-size: 8px; color: #6b6b7e; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600; }
.pl-breakdown-val { font-size: 11px; font-weight: 700; font-family: 'JetBrains Mono', monospace; margin-top: 1px; }
.pl-stage-tag { font-size: 9px; color: #6b6b7e; margin-top: 2px; }

.pl-comments-section { margin-top: 8px; padding-top: 7px; border-top: 1px solid rgba(255,255,255,0.04); display: flex; flex-direction: column; gap: 5px; }
.pl-comment { font-size: 10.5px; line-height: 1.4; padding: 5px 8px; border-radius: 6px; }
.pl-comment.scout { background: rgba(245,158,11,0.06); border-left: 2px solid rgba(245,158,11,0.4); }
.pl-comment.partner { background: rgba(139,92,246,0.08); border-left: 2px solid rgba(139,92,246,0.5); }
.pl-comment-author { font-weight: 600; font-size: 10px; margin-bottom: 1px; }
.pl-comment-author.scout-name { color: #f59e0b; }
.pl-comment-author.partner-name { color: #a78bfa; }
.pl-comment-role { font-weight: 400; opacity: 0.5; font-size: 9px; margin-left: 3px; }
.pl-comment-body { color: #c0c0c0; font-style: italic; }

.pl-add-comment-btn { margin-top: 4px; font-size: 10px; color: #8b8b9e; background: none; border: none; cursor: pointer; padding: 2px 0; opacity: 0.4; font-family: 'Inter', system-ui, sans-serif; transition: opacity 0.2s; text-align: left; }
.pl-add-comment-btn:hover { opacity: 1; }

.pl-comment-editor { margin-top: 6px; }
.pl-comment-textarea { width: 100%; background: rgba(255,255,255,0.04); border: 1px solid rgba(59,130,246,0.3); border-radius: 6px; padding: 6px 8px; color: #e6e6e6; font-size: 11px; font-family: 'Inter', system-ui, sans-serif; resize: vertical; outline: none; min-height: 38px; }
.pl-comment-textarea:focus { border-color: rgba(59,130,246,0.6); }
.pl-comment-actions { display: flex; gap: 6px; margin-top: 4px; align-items: center; }
.pl-comment-as { font-size: 10px; color: #6b6b7e; margin-left: auto; display: flex; align-items: center; gap: 4px; }
.pl-comment-as select { background: #1c2333; border: 1px solid rgba(255,255,255,0.1); border-radius: 4px; color: #e6e6e6; font-size: 10px; padding: 2px 4px; font-family: 'Inter', system-ui, sans-serif; outline: none; }
.pl-comment-as select option { background: #161b22; color: #e6e6e6; }

.pl-btn-save { font-size: 10px; padding: 4px 12px; border-radius: 5px; border: none; background: #3b82f6; color: #fff; cursor: pointer; font-weight: 600; font-family: 'Inter', system-ui, sans-serif; }
.pl-btn-save:hover { background: #2563eb; }
.pl-btn-cancel { font-size: 10px; padding: 4px 12px; border-radius: 5px; border: 1px solid rgba(255,255,255,0.1); background: transparent; color: #8b8b9e; cursor: pointer; font-family: 'Inter', system-ui, sans-serif; }

.pl-submit-btn { margin-top: 8px; width: 100%; padding: 7px 12px; border-radius: 6px; border: 1px solid rgba(139,92,246,0.3); background: rgba(139,92,246,0.1); color: #c4b5fd; font-size: 11px; font-weight: 600; cursor: pointer; font-family: 'Inter', system-ui, sans-serif; display: flex; align-items: center; justify-content: center; gap: 6px; transition: all 0.2s; }
.pl-submit-btn:hover { background: rgba(139,92,246,0.2); border-color: rgba(139,92,246,0.5); }
.pl-submit-btn svg { width: 13px; height: 13px; }
.pl-submitted-badge { margin-top: 8px; width: 100%; padding: 6px 12px; border-radius: 6px; background: rgba(139,92,246,0.08); color: #a78bfa; font-size: 10px; font-weight: 500; text-align: center; display: flex; align-items: center; justify-content: center; gap: 5px; }

.pl-empty-col { border: 1px dashed rgba(255,255,255,0.08); border-radius: 10px; padding: 24px 12px; text-align: center; color: #6b6b7e; font-size: 11px; font-family: 'Inter', system-ui, sans-serif; }
.pl-drop-line { height: 2px; background: #3b82f6; border-radius: 1px; margin: 2px 0; flex-shrink: 0; pointer-events: none; }

.pl-remove-btn { margin-top: 8px; font-size: 10px; font-weight: 500; color: rgba(248,113,113,0.5); background: none; border: none; cursor: pointer; font-family: 'Inter', system-ui, sans-serif; transition: color 0.2s; padding: 0; }
.pl-remove-btn:hover { color: #f87171; }

.pl-quickscreen-btn { margin-top: 8px; width: 100%; padding: 7px 12px; border-radius: 6px; border: 1px solid rgba(20,184,166,0.3); background: transparent; color: #2dd4bf; font-size: 11px; font-weight: 600; cursor: pointer; font-family: 'Inter', system-ui, sans-serif; display: flex; align-items: center; justify-content: center; gap: 6px; transition: all 0.2s; }
.pl-quickscreen-btn:hover:not(:disabled) { background: rgba(20,184,166,0.1); border-color: rgba(20,184,166,0.5); }
.pl-quickscreen-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.pl-quickscreen-btn.sent { color: #4ade80; border-color: rgba(74,222,128,0.3); cursor: default; opacity: 1; }

@keyframes pl-pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }
.pl-skeleton { animation: pl-pulse 2s ease-in-out infinite; }
`;

function scoreColor(s) {
  if (s >= 4.5) return '#4ade80';
  if (s >= 4.2) return '#3b82f6';
  return '#a78bfa';
}

function breakdownColor(v) {
  if (v >= 4.5) return '#4ade80';
  if (v >= 4) return '#3b82f6';
  if (v >= 3) return '#e6e6e6';
  return '#8b8b9e';
}

export default function Pipeline() {
  const [companies, setCompanies] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [draggedId, setDraggedId] = useState(null);
  const [dropTarget, setDropTarget] = useState(null);
  const [expandedId, setExpandedId] = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [commentAuthor, setCommentAuthor] = useState('Luis');
  const [commentRole, setCommentRole] = useState('scout');
  const [noteTexts, setNoteTexts] = useState({});
  const [localNotes, setLocalNotes] = useState({});
  const [quickScreenSent, setQuickScreenSent] = useState({});
  const [quickScreenSending, setQuickScreenSending] = useState({});

  async function handleQuickScreen(e, company) {
    e.stopPropagation();
    if (!company.website || quickScreenSent[company.id] || quickScreenSending[company.id]) return;
    setQuickScreenSending(prev => ({ ...prev, [company.id]: true }));
    try {
      await quickScreen(company.website, company.name);
      setQuickScreenSent(prev => ({ ...prev, [company.id]: true }));
      showToast('Sent to Quick Screen');
    } catch (err) {
      console.error('Failed to send to Quick Screen:', err);
    } finally {
      setQuickScreenSending(prev => ({ ...prev, [company.id]: false }));
    }
  }

  const loadPipeline = useCallback(() => {
    setLoading(true);
    fetchPipeline()
      .then(data => {
        const results = data.results || [];
        setCompanies(results);
        const notesMap = {};
        results.forEach(c => { notesMap[c.id] = c.pipeline_notes || []; });
        setLocalNotes(notesMap);
      })
      .catch(() => setError('Could not load pipeline'))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    fetchStats().then(setStats).catch(() => {});
    loadPipeline();
  }, [loadPipeline]);

  // --- Comment handling ---
  async function handleSaveComment(companyId) {
    const text = (noteTexts[companyId] || '').trim();
    if (!text) { setEditingId(null); return; }
    try {
      await addPipelineNote(companyId, text, commentAuthor, commentRole);
      setLocalNotes(prev => ({
        ...prev,
        [companyId]: [
          ...(prev[companyId] || []),
          { id: Date.now(), author: commentAuthor, author_role: commentRole, content: text, created_at: new Date().toISOString() },
        ],
      }));
    } catch (err) {
      console.error('Failed to add note:', err);
    }
    setEditingId(null);
    setNoteTexts(prev => ({ ...prev, [companyId]: '' }));
  }

  function toggleExpand(id) {
    if (editingId) return;
    setExpandedId(prev => (prev === id ? null : id));
  }

  // --- Group by column, sorted by position ---
  const grouped = {};
  for (const col of COLUMNS) {
    grouped[col.key] = companies
      .filter(c => c.pipeline_status === col.key)
      .sort((a, b) => (a.pipeline_position ?? 0) - (b.pipeline_position ?? 0));
  }

  // --- Move / submit / remove ---
  async function handleMove(companyId, newStatus, afterId) {
    // Calculate insertion index from the drop position
    const destCards = companies
      .filter(c => c.pipeline_status === newStatus && c.id !== companyId)
      .sort((a, b) => (a.pipeline_position ?? 0) - (b.pipeline_position ?? 0));

    let insertIdx;
    if (afterId === 'top') {
      insertIdx = 0;
    } else if (afterId != null) {
      const afterIdx = destCards.findIndex(c => c.id === afterId);
      insertIdx = afterIdx >= 0 ? afterIdx + 1 : destCards.length;
    } else {
      insertIdx = destCards.length; // append to end
    }

    // Build the new column order with the moved card spliced in
    const newOrder = [...destCards];
    newOrder.splice(insertIdx, 0, companies.find(c => c.id === companyId));
    const posMap = {};
    newOrder.forEach((c, i) => { posMap[c.id] = i; });

    // Optimistic update: set new status + re-indexed positions
    setCompanies(prev => prev.map(c => ({
      ...c,
      pipeline_status: c.id === companyId ? newStatus : c.pipeline_status,
      pipeline_position: posMap[c.id] != null ? posMap[c.id] : c.pipeline_position ?? 0,
    })));

    try {
      await moveInPipeline(companyId, newStatus, insertIdx);
    } catch (err) {
      console.error('Failed to move:', err);
      loadPipeline();
    }
  }

  async function handleSubmitToAirtable(e, company) {
    e.stopPropagation();
    await handleMove(company.id, 'submitted');
    showToast(`${company.name} submitted to Airtable`);
  }

  async function handleRemove(e, companyId) {
    e.stopPropagation();
    try {
      await removeFromPipeline(companyId);
      loadPipeline();
    } catch (err) {
      console.error('Failed to remove:', err);
    }
  }

  // --- Drag and drop ---
  function getDropPosition(colEl, clientY) {
    const cards = [...colEl.querySelectorAll('[data-company-id]')].filter(
      el => parseInt(el.dataset.companyId, 10) !== draggedId
    );
    if (!cards.length) return 'top';
    for (const card of cards) {
      const r = card.getBoundingClientRect();
      if (clientY < r.top + r.height / 2) {
        const i = cards.indexOf(card);
        return i === 0 ? 'top' : parseInt(cards[i - 1].dataset.companyId, 10);
      }
    }
    return parseInt(cards[cards.length - 1].dataset.companyId, 10);
  }

  function handleColumnDragOver(e, colKey) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const afterId = getDropPosition(e.currentTarget, e.clientY);
    setDropTarget({ colKey, afterId });
  }

  function handleColumnDragLeave(e) {
    // relatedTarget can be null in some browsers when entering a draggable child —
    // only clear when we're sure we've left the column entirely
    if (e.relatedTarget && !e.currentTarget.contains(e.relatedTarget)) {
      setDropTarget(null);
    }
  }

  function handleColumnDrop(e, colKey) {
    e.preventDefault();
    const target = dropTarget;
    setDropTarget(null);
    if (draggedId !== null) {
      handleMove(draggedId, colKey, target?.afterId);
      setDraggedId(null);
    }
  }

  function handleCardDragStart(e, companyId) {
    setDraggedId(companyId);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', String(companyId));
  }

  function handleCardDragEnd() {
    setDraggedId(null);
    setDropTarget(null);
  }

  const totalInPipeline = companies.length;

  // --- Render a single card ---
  function renderCard(company, colKey) {
    const isExpanded = expandedId === company.id;
    const isPassed = colKey === 'passed';
    const isShortlisted = colKey === 'shortlisted';
    const isSubmitted = colKey === 'submitted';
    const isDragging = draggedId === company.id;
    const score = company.athena_score != null ? company.athena_score : 0;
    const notes = localNotes[company.id] || [];
    const isEditing = editingId === company.id;
    const bd = company.score_breakdown?.components || {};

    let cardClass = 'pl-card';
    if (isPassed) cardClass += ' passed';
    if (isShortlisted) cardClass += ' shortlisted';
    if (isSubmitted) cardClass += ' submitted';
    if (isDragging) cardClass += ' dragging';

    return (
      <div
        key={company.id}
        className={cardClass}
        draggable="true"
        data-company-id={company.id}
        onDragStart={e => handleCardDragStart(e, company.id)}
        onDragEnd={handleCardDragEnd}
        onDragOver={e => e.preventDefault()}
        onClick={() => toggleExpand(company.id)}
      >
        {/* Top: name + score */}
        <div className="pl-card-top">
          <span className="pl-card-name">{company.name}</span>
          <span className="pl-card-score" style={{ color: scoreColor(score) }}>{score.toFixed(1)}</span>
        </div>

        {/* Description */}
        {company.description && (
          <p className={`pl-card-desc${isExpanded ? '' : ' clamp'}`}>{company.description}</p>
        )}

        {/* Tags */}
        <div className="pl-tags">
          {company.sector && <span className="pl-tag">{company.sector}</span>}
          {company.geography && <span className="pl-tag">{company.geography}</span>}
          {company.programs?.length > 0 && (
            <span className="pl-tag program">
              {company.programs[0].program_name}
              {company.programs[0].cohort ? ` ${company.programs[0].cohort}` : ''}
            </span>
          )}
        </div>

        {/* Expanded: breakdown + stage */}
        {isExpanded && (
          <div onClick={e => e.stopPropagation()}>
            <div className="pl-breakdown">
              {[
                { key: 'thesis', label: 'Thesis' },
                { key: 'team', label: 'Team' },
                { key: 'program', label: 'Prog' },
                { key: 'traction', label: 'Tract' },
                { key: 'data', label: 'Data' },
              ].map(({ key, label }) => {
                const comp = bd[key];
                const v = comp?.score;
                return (
                  <div key={key} className="pl-breakdown-item">
                    <div className="pl-breakdown-label">{label}</div>
                    <div className="pl-breakdown-val" style={{ color: v != null ? breakdownColor(v) : '#8b8b9e' }}>
                      {v != null ? `${v}/5` : '\u2014'}
                    </div>
                  </div>
                );
              })}
            </div>
            {company.stage && <div className="pl-stage-tag">{company.stage}</div>}
          </div>
        )}

        {/* Comments — always visible */}
        {(notes.length > 0 || isEditing) ? (
          <div className="pl-comments-section" onClick={e => e.stopPropagation()}>
            {notes.map((n, i) => {
              const isPartner = n.author_role === 'partner';
              return (
                <div key={n.id || i} className={`pl-comment ${isPartner ? 'partner' : 'scout'}`}>
                  <div className={`pl-comment-author ${isPartner ? 'partner-name' : 'scout-name'}`}>
                    {n.author}<span className="pl-comment-role">{n.author_role}</span>
                  </div>
                  <div className="pl-comment-body">{n.content}</div>
                </div>
              );
            })}
            {isEditing ? (
              <div className="pl-comment-editor">
                <textarea
                  className="pl-comment-textarea"
                  rows={2}
                  placeholder="Add a comment..."
                  value={noteTexts[company.id] || ''}
                  onChange={e => setNoteTexts(prev => ({ ...prev, [company.id]: e.target.value }))}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); e.stopPropagation(); handleSaveComment(company.id); }
                    if (e.key === 'Escape') { e.stopPropagation(); setEditingId(null); setNoteTexts(prev => ({ ...prev, [company.id]: '' })); }
                  }}
                  autoFocus
                />
                <div className="pl-comment-actions">
                  <button className="pl-btn-save" onClick={e => { e.stopPropagation(); handleSaveComment(company.id); }}>Post</button>
                  <button className="pl-btn-cancel" onClick={e => { e.stopPropagation(); setEditingId(null); setNoteTexts(prev => ({ ...prev, [company.id]: '' })); }}>Cancel</button>
                  <span className="pl-comment-as">
                    as{' '}
                    <select
                      value={`${commentAuthor}|${commentRole}`}
                      onChange={e => { const [a, r] = e.target.value.split('|'); setCommentAuthor(a); setCommentRole(r); }}
                      onClick={e => e.stopPropagation()}
                    >
                      <option value="Luis|scout">Luis (scout)</option>
                      <option value="Yariv|partner">Yariv (partner)</option>
                      <option value="Matthias|partner">Matthias (partner)</option>
                      <option value="Celina|scout">Celina (scout)</option>
                    </select>
                  </span>
                </div>
              </div>
            ) : (
              <button className="pl-add-comment-btn" onClick={e => { e.stopPropagation(); setEditingId(company.id); }}>+ Add comment</button>
            )}
          </div>
        ) : (
          <button className="pl-add-comment-btn" onClick={e => { e.stopPropagation(); setEditingId(company.id); }} style={{ marginTop: 8 }}>+ Add comment</button>
        )}

        {/* Submit to Airtable (shortlisted only) */}
        {isShortlisted && (
          <button className="pl-submit-btn" onClick={e => handleSubmitToAirtable(e, company)}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M22 2L11 13" /><path d="M22 2L15 22L11 13L2 9L22 2Z" /></svg>
            Submit to Airtable
          </button>
        )}

        {/* Submitted badge */}
        {isSubmitted && (
          <div className="pl-submitted-badge">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M20 6L9 17L4 12" /></svg>
            Submitted to Airtable
          </div>
        )}

        {/* Quick Screen — expanded only */}
        {isExpanded && (
          <button
            className={`pl-quickscreen-btn${quickScreenSent[company.id] ? ' sent' : ''}`}
            onClick={e => handleQuickScreen(e, company)}
            disabled={!company.website || quickScreenSent[company.id] || quickScreenSending[company.id]}
            title={!company.website ? 'No website available' : undefined}
          >
            {quickScreenSent[company.id]
              ? 'Sent ✓'
              : (quickScreenSending[company.id] ? 'Sending...' : 'Quick Screen ↗')}
          </button>
        )}

        {/* Remove — expanded only */}
        {isExpanded && (
          <button className="pl-remove-btn" onClick={e => handleRemove(e, company.id)}>
            Remove from pipeline
          </button>
        )}
      </div>
    );
  }

  // --- Render a column with cards + drop line ---
  function renderColumn(col) {
    const colCompanies = grouped[col.key] || [];
    const isDragOver = dropTarget?.colKey === col.key;

    const cardElements = [];
    if (colCompanies.length > 0) {
      if (isDragOver && dropTarget.afterId === 'top') {
        cardElements.push(<div key="drop-line-top" className="pl-drop-line" />);
      }
      colCompanies.forEach(c => {
        cardElements.push(renderCard(c, col.key));
        if (isDragOver && dropTarget.afterId === c.id) {
          cardElements.push(<div key={`drop-line-${c.id}`} className="pl-drop-line" />);
        }
      });
    }

    return (
      <div key={col.key} className="pl-column">
        <div className="pl-col-header" style={{ borderBottom: `2px solid ${col.color}` }}>
          <span className="pl-col-label">{col.label}</span>
          <span className="pl-col-count" style={{ background: `${col.color}15`, color: col.color }}>{colCompanies.length}</span>
          <span className="pl-col-desc">{col.desc}</span>
        </div>
        <div
          className={`pl-col-cards${isDragOver ? ' drag-over' : ''}`}
          data-col={col.key}
          onDragOver={e => handleColumnDragOver(e, col.key)}
          onDragLeave={handleColumnDragLeave}
          onDrop={e => handleColumnDrop(e, col.key)}
        >
          {colCompanies.length > 0 ? cardElements : (
            <div className="pl-empty-col">Drag companies here</div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div style={{ minHeight: '100vh', background: '#0a0a0f', paddingTop: 56 }}>
      <TopBar stats={stats} />
      <style>{PIPELINE_CSS}</style>

      {/* Stats bar */}
      {totalInPipeline > 0 && (
        <div className="pl-stats-bar">
          <span><strong>{totalInPipeline}</strong> companies tracked</span>
          <span className="pl-stats-sep">|</span>
          {COLUMNS.map(col => (
            <span key={col.key}>
              <span className="pl-stat-dot" style={{ background: col.color }} />
              {(grouped[col.key] || []).length} {col.label.toLowerCase()}
            </span>
          ))}
          <span className="pl-stats-right">Score threshold: &ge; 4.0</span>
        </div>
      )}

      {/* Board */}
      {loading ? (
        <div className="pl-board">
          {COLUMNS.map(col => (
            <div key={col.key} className="pl-column">
              <div className="pl-col-header" style={{ borderBottom: `2px solid ${col.color}` }}>
                <span className="pl-col-label">{col.label}</span>
                <span className="pl-col-count" style={{ background: `${col.color}15`, color: col.color }}>-</span>
              </div>
              <div className="pl-col-cards">
                {[1, 2].map(i => (
                  <div key={i} className="pl-skeleton" style={{ height: 112, borderRadius: 10, background: '#161b22', border: '1px solid rgba(255,255,255,0.06)' }} />
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : error ? (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '96px 0', color: '#8b8b9e' }}>
          <p style={{ fontSize: 14, fontFamily: "'Inter', system-ui, sans-serif" }}>{error}</p>
        </div>
      ) : totalInPipeline === 0 ? (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '96px 0', textAlign: 'center', fontFamily: "'Inter', system-ui, sans-serif" }}>
          <p style={{ fontSize: 14, color: '#e6e6e6', fontWeight: 500, marginBottom: 8 }}>No companies in the pipeline yet</p>
          <p style={{ fontSize: 13, color: '#8b8b9e', maxWidth: 400 }}>
            Browse the <Link to="/" style={{ color: '#3b82f6', textDecoration: 'none' }}>Dashboard</Link> or{' '}
            <Link to="/top20" style={{ color: '#3b82f6', textDecoration: 'none' }}>Top 20</Link> to find companies,
            then click &ldquo;Add to Pipeline&rdquo; to start tracking them here.
          </p>
        </div>
      ) : (
        <div className="pl-board">
          {COLUMNS.map(col => renderColumn(col))}
        </div>
      )}
    </div>
  );
}
