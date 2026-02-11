import { Link } from 'react-router-dom';

export default function Footer() {
  return (
    <footer className="border-t border-athena-border mt-12 py-6">
      <div className="max-w-[1440px] mx-auto px-6 flex items-center justify-between text-[12px] text-athena-muted/50">
        <span className="font-sans">
          Built by <span className="text-athena-muted">Luis</span>
          <span className="mx-1.5">·</span>
          <span className="text-athena-muted">Ellipsis Ventures</span> Scout
        </span>
        <div className="flex items-center gap-3">
          <Link to="/about" className="hover:text-athena-accent text-athena-muted/50 no-underline">
            About & Methodology
          </Link>
          <span className="text-athena-border">·</span>
          <span className="font-mono">Data updated daily</span>
        </div>
      </div>
    </footer>
  );
}
