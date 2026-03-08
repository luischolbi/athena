import { useEffect, useRef } from 'react';

function Select({ value, onChange, options, placeholder }) {
  const isActive = value && value !== '';
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={`h-8 px-3 pr-7 rounded-full text-[13px] font-sans cursor-pointer
                 appearance-none border focus:outline-none focus:ring-1 focus:ring-athena-accent/40
                 bg-[url('data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%2210%22%20height%3D%2210%22%20viewBox%3D%220%200%2012%2012%22%3E%3Cpath%20fill%3D%22%238b8b9e%22%20d%3D%22M6%208L1%203h10z%22%2F%3E%3C%2Fsvg%3E')]
                 bg-[length:10px] bg-[right_8px_center] bg-no-repeat
                 ${isActive
                   ? 'bg-athena-accent/10 border-athena-accent/30 text-athena-accent'
                   : 'bg-athena-card border-athena-border text-athena-muted hover:border-athena-muted/30'
                 }`}
    >
      <option value="">{placeholder}</option>
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>{opt.label}</option>
      ))}
    </select>
  );
}

export default function FilterBar({ filters, filterOptions, onFilterChange, total, totalUnfiltered, showing, onSearchChange, hideInactive, onHideInactiveChange, hideUnverified, onHideUnverifiedChange, onExport, exporting }) {
  const debounceRef = useRef(null);

  useEffect(() => {
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, []);

  function handleSearch(e) {
    const val = e.target.value;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => onSearchChange(val), 300);
  }

  const programOptions = (filterOptions.programs || []).map((p) => ({ value: p, label: p }));
  const sectorOptions = (filterOptions.sectors || []).map((s) => ({ value: s, label: s }));
  const geoOptions = (filterOptions.geographies || []).map((g) => ({ value: g, label: g }));
  const stageOptions = (filterOptions.stages || []).map((s) => ({ value: s, label: s }));
  const scoreOptions = [
    { value: '2.0', label: '2.0+' },
    { value: '2.5', label: '2.5+' },
    { value: '3.0', label: '3.0+ (On Radar)' },
    { value: '3.5', label: '3.5+ (Investigate)' },
    { value: '4.0', label: '4.0+ (High Priority)' },
    { value: '4.5', label: '4.5+' },
  ];
  const yearOptions = (filterOptions.cohort_years || []).map((y) => ({ value: y, label: y }));
  const tierOptions = [
    { value: '1', label: 'Curated' },
    { value: '2', label: 'Standard' },
    { value: '3', label: 'Discovery' },
  ];

  const activeFilterCount = Object.values(filters).filter(Boolean).length +
    (hideInactive ? 1 : 0) + (hideUnverified ? 1 : 0);

  return (
    <div className="sticky top-14 z-40 bg-athena-bg/95 backdrop-blur-md border-b border-athena-border py-3">
      <div className="max-w-[1440px] mx-auto px-6">
        <div className="flex items-center gap-2 flex-wrap">
          <Select value={filters.program || ''} onChange={(v) => onFilterChange('program', v)} options={programOptions} placeholder="Program" />
          <Select value={filters.sector || ''} onChange={(v) => onFilterChange('sector', v)} options={sectorOptions} placeholder="Sector" />
          <Select value={filters.geography || ''} onChange={(v) => onFilterChange('geography', v)} options={geoOptions} placeholder="Geography" />
          <Select value={filters.stage || ''} onChange={(v) => onFilterChange('stage', v)} options={stageOptions} placeholder="Stage" />
          <Select value={filters.min_score || ''} onChange={(v) => onFilterChange('min_score', v)} options={scoreOptions} placeholder="Min Score" />
          <Select value={filters.cohort_year || ''} onChange={(v) => onFilterChange('cohort_year', v)} options={yearOptions} placeholder="Year" />
          <Select value={filters.data_tier || ''} onChange={(v) => onFilterChange('data_tier', v)} options={tierOptions} placeholder="Data Tier" />

          {/* Toggle buttons */}
          <button
            onClick={() => onHideUnverifiedChange(!hideUnverified)}
            className={`h-8 px-3 rounded-full text-[13px] font-sans border transition-colors
              ${hideUnverified
                ? 'bg-athena-accent/10 border-athena-accent/30 text-athena-accent'
                : 'bg-athena-card border-athena-border text-athena-muted hover:border-athena-muted/30'
              }`}
          >
            {hideUnverified ? 'Curated only' : 'All tiers'}
          </button>
          <button
            onClick={() => onHideInactiveChange(!hideInactive)}
            className={`h-8 px-3 rounded-full text-[13px] font-sans border transition-colors
              ${hideInactive
                ? 'bg-athena-accent/10 border-athena-accent/30 text-athena-accent'
                : 'bg-athena-card border-athena-border text-athena-muted hover:border-athena-muted/30'
              }`}
          >
            {hideInactive ? 'Active only' : 'All statuses'}
          </button>

          {/* Clear filters */}
          {activeFilterCount > 2 && (
            <button
              onClick={() => {
                onFilterChange('program', '');
                onFilterChange('sector', '');
                onFilterChange('geography', '');
                onFilterChange('stage', '');
                onFilterChange('min_score', '');
                onFilterChange('cohort_year', '');
                onFilterChange('data_tier', '');
              }}
              className="h-8 px-3 rounded-full text-[12px] font-sans text-athena-muted/60 hover:text-athena-accent transition-colors"
            >
              Clear filters
            </button>
          )}

          {/* Export */}
          {total > 0 && (
            <button
              onClick={onExport}
              disabled={exporting}
              className="h-8 px-3 rounded-full text-[13px] font-sans border border-athena-border bg-athena-card
                         text-athena-muted hover:text-athena-accent hover:border-athena-accent/30
                         disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center gap-1.5"
            >
              {exporting ? (
                <>
                  <svg className="animate-spin w-3 h-3" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                  </svg>
                  Exporting...
                </>
              ) : (
                <>
                  <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                  </svg>
                  Export CSV
                </>
              )}
            </button>
          )}

          {/* Search */}
          <div className="relative ml-auto">
            <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-athena-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
            </svg>
            <input
              type="text"
              placeholder="Search companies..."
              onChange={handleSearch}
              className="h-8 pl-8 pr-4 w-52 rounded-full border border-athena-border bg-athena-card text-[13px] text-athena-text
                         placeholder:text-athena-muted/50 focus:outline-none focus:ring-1 focus:ring-athena-accent/40 focus:border-athena-accent/30 font-sans"
            />
          </div>
        </div>

        <div className="mt-2 text-[11px] text-athena-muted font-mono">
          Showing {showing} of {total.toLocaleString()} companies
          {totalUnfiltered > 0 && totalUnfiltered !== total && (
            <span className="text-athena-muted/40"> ({totalUnfiltered.toLocaleString()} total)</span>
          )}
        </div>
      </div>
    </div>
  );
}
