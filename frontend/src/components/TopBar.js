import { Link, useLocation } from 'react-router-dom';

export default function TopBar({ stats }) {
  const location = useLocation();
  const isTop20 = location.pathname === '/top20';
  const isDashboard = location.pathname === '/';
  const isNew = location.pathname === '/new';
  const isPipeline = location.pathname === '/pipeline';

  return (
    <header className="fixed top-0 left-0 right-0 z-50 bg-athena-bg/95 backdrop-blur-md border-b border-athena-border h-14">
      <div className="max-w-[1440px] mx-auto px-6 h-full flex items-center justify-between">
        {/* Logo + Nav */}
        <div className="flex items-center gap-5">
          <Link to="/" className="flex items-center gap-2.5 no-underline group">
            <span className="text-athena-accent text-lg">◆</span>
            <span className="font-mono font-bold text-athena-text tracking-[0.15em] text-[13px]">
              ATHENA
            </span>
          </Link>

          <div className="hidden sm:flex items-center gap-1 pl-4 border-l border-athena-border">
            <Link
              to="/"
              className={`px-3 py-1 rounded-md text-[12px] font-sans no-underline transition-colors ${
                isDashboard
                  ? 'text-athena-accent bg-athena-accent/10'
                  : 'text-athena-muted hover:text-athena-text'
              }`}
            >
              Dashboard
            </Link>
            <Link
              to="/top20"
              className={`px-3 py-1 rounded-md text-[12px] font-sans no-underline transition-colors ${
                isTop20
                  ? 'text-athena-accent bg-athena-accent/10'
                  : 'text-athena-muted hover:text-athena-text'
              }`}
            >
              Top 20
            </Link>
            <Link
              to="/new"
              className={`px-3 py-1 rounded-md text-[12px] font-sans no-underline transition-colors ${
                isNew
                  ? 'text-athena-accent bg-athena-accent/10'
                  : 'text-athena-muted hover:text-athena-text'
              }`}
            >
              New
            </Link>
            <Link
              to="/pipeline"
              className={`px-3 py-1 rounded-md text-[12px] font-sans no-underline transition-colors ${
                isPipeline
                  ? 'text-athena-accent bg-athena-accent/10'
                  : 'text-athena-muted hover:text-athena-text'
              }`}
            >
              Pipeline
            </Link>
          </div>
        </div>

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
