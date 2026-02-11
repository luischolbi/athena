import { Link } from 'react-router-dom';

export default function TopBar({ stats }) {
  return (
    <header className="fixed top-0 left-0 right-0 z-50 bg-athena-bg/95 backdrop-blur-md border-b border-athena-border h-14">
      <div className="max-w-[1440px] mx-auto px-6 h-full flex items-center justify-between">
        {/* Logo */}
        <Link to="/" className="flex items-center gap-2.5 no-underline group">
          <span className="text-athena-accent text-lg">◆</span>
          <span className="font-mono font-bold text-athena-text tracking-[0.15em] text-[13px]">
            ATHENA
          </span>
          <span className="hidden lg:block text-[13px] text-athena-muted font-sans ml-2 pl-3 border-l border-athena-border">
            European Pre-Seed Intelligence
          </span>
        </Link>

        {/* Stats */}
        <div className="font-mono text-xs text-athena-muted">
          {stats ? (
            <>
              <span className="text-athena-text">{stats.total_companies.toLocaleString()}</span>
              {' companies'}
              <span className="mx-2 text-athena-border">·</span>
              <span className="text-athena-text">{stats.source_count || 7}</span>
              {' sources'}
              <span className="mx-2 text-athena-border">·</span>
              {'Updated today'}
            </>
          ) : (
            <span className="animate-pulse">Loading...</span>
          )}
        </div>
      </div>
    </header>
  );
}
