export default function StatsOverview({ stats }) {
  if (!stats) return null;

  const topSectors = Object.entries(stats.by_sector || {})
    .filter(([k]) => k !== 'Unknown')
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);

  const topCountries = Object.entries(stats.by_geography || {})
    .filter(([k]) => k !== 'Unknown')
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);

  const tierLabels = { '1': 'Curated', '2': 'Standard', '3': 'Discovery' };

  const priorityConfig = [
    { key: 'high', label: 'High Priority', cls: 'text-emerald-400' },
    { key: 'investigate', label: 'Investigate', cls: 'text-blue-400' },
    { key: 'radar', label: 'On Radar', cls: 'text-amber-400' },
    { key: 'low', label: 'Low Priority', cls: 'text-athena-muted/60' },
  ];

  return (
    <div className="max-w-[1440px] mx-auto px-6 pt-5">
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
        {/* Active Companies */}
        <StatCard
          label="Active Companies"
          value={stats.active_verified?.toLocaleString() || '—'}
          sub={`${stats.total_companies?.toLocaleString()} total`}
        />

        {/* By Priority */}
        <div className="p-3.5 rounded-xl bg-athena-card border border-athena-border">
          <span className="text-[10px] font-mono text-athena-muted uppercase tracking-wider">By Priority</span>
          <div className="mt-2 space-y-1.5">
            {priorityConfig.map(({ key, label, cls }) => (
              <div key={key} className="flex items-center justify-between">
                <span className={`text-[11px] font-medium ${cls}`}>{label}</span>
                <span className="font-mono text-[12px] text-athena-text">
                  {(stats.by_priority?.[key] || 0).toLocaleString()}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* By Tier */}
        <div className="p-3.5 rounded-xl bg-athena-card border border-athena-border">
          <span className="text-[10px] font-mono text-athena-muted uppercase tracking-wider">By Tier</span>
          <div className="mt-2 space-y-1.5">
            {Object.entries(stats.by_tier || {})
              .filter(([k]) => ['1', '2', '3'].includes(k))
              .sort(([a], [b]) => Number(a) - Number(b))
              .map(([tier, count]) => (
                <div key={tier} className="flex items-center justify-between">
                  <span className={`text-[11px] font-medium ${
                    tier === '1' ? 'text-emerald-400' : tier === '2' ? 'text-blue-400' : 'text-athena-muted/60'
                  }`}>
                    {tierLabels[tier]}
                  </span>
                  <span className="font-mono text-[12px] text-athena-text">{count.toLocaleString()}</span>
                </div>
              ))}
          </div>
        </div>

        {/* Top Sectors */}
        <div className="p-3.5 rounded-xl bg-athena-card border border-athena-border">
          <span className="text-[10px] font-mono text-athena-muted uppercase tracking-wider">Top Sectors</span>
          <div className="mt-2 space-y-1.5">
            {topSectors.map(([sector, count]) => (
              <div key={sector} className="flex items-center justify-between gap-2">
                <span className="text-[11px] text-athena-muted truncate">{sector}</span>
                <span className="font-mono text-[11px] text-athena-text flex-shrink-0">{count}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Top Countries */}
        <div className="p-3.5 rounded-xl bg-athena-card border border-athena-border">
          <span className="text-[10px] font-mono text-athena-muted uppercase tracking-wider">Top Countries</span>
          <div className="mt-2 space-y-1.5">
            {topCountries.map(([geo, count]) => (
              <div key={geo} className="flex items-center justify-between gap-2">
                <span className="text-[11px] text-athena-muted truncate">{geo}</span>
                <span className="font-mono text-[11px] text-athena-text flex-shrink-0">{count}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Sources */}
        <StatCard
          label="Sources"
          value={stats.source_count || '—'}
          sub={`${stats.new_this_week || 0} new this week`}
        />
      </div>
    </div>
  );
}

function StatCard({ label, value, sub }) {
  return (
    <div className="p-3.5 rounded-xl bg-athena-card border border-athena-border">
      <span className="text-[10px] font-mono text-athena-muted uppercase tracking-wider">{label}</span>
      <div className="mt-1.5">
        <span className="font-mono font-bold text-[22px] text-athena-text leading-none">{value}</span>
      </div>
      {sub && (
        <span className="text-[11px] text-athena-muted/50 mt-1 block">{sub}</span>
      )}
    </div>
  );
}
