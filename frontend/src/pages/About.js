import { Link } from 'react-router-dom';

function Section({ title, children }) {
  return (
    <section className="mb-12">
      <h2 className="text-[11px] font-mono font-medium text-athena-muted uppercase tracking-wider mb-4 pb-3 border-b border-athena-border">
        {title}
      </h2>
      {children}
    </section>
  );
}

export default function About() {
  return (
    <div className="min-h-screen bg-athena-bg">
      {/* Header */}
      <header className="border-b border-athena-border">
        <div className="max-w-[700px] mx-auto px-6 py-8">
          <Link to="/" className="inline-flex items-center gap-1.5 text-[13px] text-athena-accent hover:text-athena-accent-hover font-medium mb-6 no-underline">
            ← Dashboard
          </Link>
          <div className="flex items-center gap-3">
            <span className="text-athena-accent text-lg">◆</span>
            <div>
              <h1 className="text-xl font-semibold text-athena-text font-sans">Athena</h1>
              <p className="text-athena-muted text-[13px] font-sans">About & Methodology</p>
            </div>
          </div>
        </div>
      </header>

      {/* Content */}
      <main className="max-w-[700px] mx-auto px-6 py-10">
        <Section title="What is Athena?">
          <p className="text-athena-muted text-[14px] leading-relaxed">
            Athena is a deal intelligence platform that tracks early-stage European startups across
            accelerators, news sources, and tech communities. It automatically discovers companies
            from multiple signal layers, deduplicates them, and calculates a heat score to surface
            the most promising pre-seed opportunities.
          </p>
          <p className="text-athena-muted text-[14px] leading-relaxed mt-3">
            Built as a scouting tool for venture capital, Athena replaces manual monitoring of
            dozens of sources with a single, always-updated feed ranked by signal strength.
          </p>
        </Section>

        <Section title="The Two-Layer Model">
          <div className="grid md:grid-cols-2 gap-4">
            <div className="p-5 rounded-xl bg-athena-card border border-athena-border">
              <h3 className="font-semibold text-athena-text text-[14px] mb-2">Curated Layer</h3>
              <p className="text-[13px] text-athena-muted leading-relaxed">
                Companies that have passed an external selection process — accepted into an
                accelerator, spun out of a university lab, or received competitive program funding.
                High-signal, low-noise coverage of vetted startups.
              </p>
              <p className="text-[11px] text-athena-muted/40 mt-3 font-mono">
                Venture Kick · ETH AI Center · Entrepreneur First · Seedcamp · Cambridge Enterprise · Imperial Enterprise Lab
              </p>
            </div>
            <div className="p-5 rounded-xl bg-athena-card border border-athena-border">
              <h3 className="font-semibold text-athena-text text-[14px] mb-2">Real-Time Layer</h3>
              <p className="text-[13px] text-athena-muted leading-relaxed">
                Companies generating organic buzz — trending on HackerNews, launching on
                ProductHunt, or covered by European tech press. Captures momentum
                signals and community traction as they happen.
              </p>
              <p className="text-[11px] text-athena-muted/40 mt-3 font-mono">
                HackerNews · ProductHunt · Sifted · Tech.eu · EU-Startups · TechCrunch
              </p>
            </div>
          </div>
        </Section>

        <Section title="Heat Score Methodology">
          <p className="text-athena-muted text-[14px] leading-relaxed mb-5">
            Every company receives a heat score from 1 to 10 based on four weighted components.
            The score is recalculated after every pipeline run.
          </p>

          {/* Component 1: Program Pedigree */}
          <div className="mb-5">
            <h3 className="text-[13px] font-medium text-athena-text mb-2">1. Program Pedigree <span className="font-mono text-athena-accent">(up to 4 pts)</span></h3>
            <div className="space-y-0">
              {[
                { rule: 'Tier A', pts: '4', desc: 'EF, Seedcamp, YC, Techstars, VK Stage 2/3' },
                { rule: 'Tier B', pts: '3', desc: 'Venture Kick (Stage 1), ETH AI Center' },
                { rule: 'Tier C', pts: '2', desc: 'Cambridge Enterprise, Imperial Enterprise Lab' },
                { rule: 'Tier D', pts: '1', desc: 'Any other tracked program' },
                { rule: 'Multi-program', pts: '+1', desc: 'In 2+ different programs (capped at 4 total)' },
              ].map((item, i) => (
                <div key={i} className="flex items-start gap-4 py-2 border-b border-athena-border/50 last:border-0">
                  <span className="font-mono text-[12px] text-athena-accent font-bold w-10 flex-shrink-0 text-right pt-0.5">
                    {item.pts}
                  </span>
                  <div>
                    <span className="text-[13px] font-medium text-athena-text">{item.rule}</span>
                    <p className="text-[12px] text-athena-muted/50 mt-0.5">{item.desc}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Component 2: Community Buzz */}
          <div className="mb-5">
            <h3 className="text-[13px] font-medium text-athena-text mb-2">2. Community Buzz <span className="font-mono text-athena-accent">(up to 3 pts)</span></h3>
            <div className="space-y-0">
              {[
                { rule: 'HN viral', pts: '3', desc: '300+ points or 100+ comments' },
                { rule: 'HN traction', pts: '2', desc: '100+ points or 50+ comments' },
                { rule: 'HN signal', pts: '1', desc: 'Any HackerNews appearance' },
                { rule: 'ProductHunt', pts: '+1', desc: 'Launched on ProductHunt' },
                { rule: 'Press mention', pts: '+1 ea', desc: 'Sifted, Tech.eu, EU-Startups, TechCrunch' },
              ].map((item, i) => (
                <div key={i} className="flex items-start gap-4 py-2 border-b border-athena-border/50 last:border-0">
                  <span className="font-mono text-[12px] text-athena-accent font-bold w-10 flex-shrink-0 text-right pt-0.5">
                    {item.pts}
                  </span>
                  <div>
                    <span className="text-[13px] font-medium text-athena-text">{item.rule}</span>
                    <p className="text-[12px] text-athena-muted/50 mt-0.5">{item.desc}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Component 3: Cross-Source */}
          <div className="mb-5">
            <h3 className="text-[13px] font-medium text-athena-text mb-2">3. Cross-Source Appearances <span className="font-mono text-athena-accent">(up to 2 pts)</span></h3>
            <div className="space-y-0">
              {[
                { rule: '3+ sources', pts: '2', desc: 'Appears across 3 or more distinct sources' },
                { rule: '2 sources', pts: '1', desc: 'Appears across 2 distinct sources' },
              ].map((item, i) => (
                <div key={i} className="flex items-start gap-4 py-2 border-b border-athena-border/50 last:border-0">
                  <span className="font-mono text-[12px] text-athena-accent font-bold w-10 flex-shrink-0 text-right pt-0.5">
                    {item.pts}
                  </span>
                  <div>
                    <span className="text-[13px] font-medium text-athena-text">{item.rule}</span>
                    <p className="text-[12px] text-athena-muted/50 mt-0.5">{item.desc}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Component 4: Recency */}
          <div className="mb-5">
            <h3 className="text-[13px] font-medium text-athena-text mb-2">4. Recency Boost <span className="font-mono text-athena-accent">(up to 1 pt)</span></h3>
            <div className="space-y-0">
              <div className="flex items-start gap-4 py-2">
                <span className="font-mono text-[12px] text-athena-accent font-bold w-10 flex-shrink-0 text-right pt-0.5">
                  +1
                </span>
                <div>
                  <span className="text-[13px] font-medium text-athena-text">Recent signal</span>
                  <p className="text-[12px] text-athena-muted/50 mt-0.5">Any signal detected within the last 7 days</p>
                </div>
              </div>
            </div>
          </div>

          <p className="text-[11px] text-athena-muted/40 mt-2 font-mono">
            Max score = 10. Companies with ↑ are "rising" — score increased by 2+ since last run.
          </p>
        </Section>

        <Section title="Cross-Layer Matches">
          <p className="text-athena-muted text-[14px] leading-relaxed">
            The most valuable signal: a company that appears in both the curated layer
            (e.g. accepted into Seedcamp) AND the real-time layer (e.g. trending on
            HackerNews or covered by Sifted). This convergence of institutional validation
            and organic traction is marked with a ✦ indicator and a scoring bonus.
          </p>
        </Section>

        <Section title="Data Sources">
          <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
            {[
              { name: 'Venture Kick', type: 'Curated', geo: 'CH' },
              { name: 'ETH AI Center', type: 'Curated', geo: 'CH' },
              { name: 'Entrepreneur First', type: 'Curated', geo: 'UK/EU' },
              { name: 'Seedcamp', type: 'Curated', geo: 'UK/EU' },
              { name: 'Cambridge Enterprise', type: 'Curated', geo: 'UK' },
              { name: 'Imperial Enterprise Lab', type: 'Curated', geo: 'UK' },
              { name: 'HackerNews', type: 'Real-Time', geo: 'Global' },
              { name: 'ProductHunt', type: 'Real-Time', geo: 'Global' },
              { name: 'Sifted', type: 'Press', geo: 'EU' },
              { name: 'Tech.eu', type: 'Press', geo: 'EU' },
              { name: 'EU-Startups', type: 'Press', geo: 'EU' },
              { name: 'TechCrunch', type: 'Press', geo: 'Global' },
            ].map((s, i) => (
              <div key={i} className="p-3 rounded-lg bg-athena-card border border-athena-border">
                <div className="font-medium text-athena-text text-[13px]">{s.name}</div>
                <div className="text-[11px] text-athena-muted/40 font-mono mt-1">{s.type} · {s.geo}</div>
              </div>
            ))}
          </div>
        </Section>

        <Section title="What I'd Build Next">
          <ul className="space-y-2">
            {[
              'Automated weekly email digest of top-scoring new companies',
              'LinkedIn and Crunchbase enrichment for founder backgrounds',
              'Real-time alerts when a tracked company crosses a score threshold',
              'Integration with deal flow CRM (Affinity, Attio)',
              'More European accelerators: Antler, Plug and Play, Station F',
              'Sentiment analysis on press coverage and HN comments',
              'Founder-market fit scoring based on academic background vs. sector',
              'Historical trend tracking — how scores evolve over time',
            ].map((item, i) => (
              <li key={i} className="flex items-start gap-2 text-[13px] text-athena-muted">
                <span className="text-athena-accent mt-0.5">→</span>
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </Section>

        <Section title="About">
          <div className="p-5 rounded-xl bg-athena-card border border-athena-border">
            <p className="text-athena-muted text-[14px] leading-relaxed">
              Built by <span className="text-athena-text font-medium">Luis</span>, Scout at{' '}
              <span className="text-athena-text font-medium">Ellipsis Ventures</span>.
            </p>
            <p className="text-athena-muted/60 text-[13px] mt-3 leading-relaxed">
              Athena was built to solve a real problem: the European pre-seed landscape is
              fragmented across dozens of accelerators, university programs, and local ecosystems.
              No single source gives you the full picture. Athena brings these signals together,
              deduplicates them, and surfaces the companies generating the most momentum —
              so scouts and investors can focus on the conversations that matter.
            </p>
          </div>
        </Section>

        <div className="text-center pt-2 pb-8">
          <Link
            to="/"
            className="inline-flex items-center gap-1.5 px-5 py-2 rounded-lg bg-athena-accent/10 border border-athena-accent/20 text-athena-accent text-[13px] font-medium hover:bg-athena-accent/15 no-underline"
          >
            ← Back to Dashboard
          </Link>
        </div>
      </main>
    </div>
  );
}
