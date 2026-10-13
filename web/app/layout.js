export const metadata = {
  title: "TenderOps — Agentic tender analysis & bid prep",
  description:
    "AI agents that read tenders, verify evidence, assess compliance, and draft bids.",
};

import "./globals.css";

function LinkedInIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 0-1.138.92-2.063 2.063-2.063 1.14 0 2.064.925 2.064 2.063 0 1.139-.925 2.065-2.064 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.225 0z" />
    </svg>
  );
}

export default function RootLayout({ children }) {
  return (
    <html lang="en" className="dark">
      <body>
        {children}
        <footer className="mx-auto max-w-6xl px-4 pb-8">
          <div className="flex flex-wrap items-center justify-center gap-x-2 gap-y-1 rounded-2xl border border-white/10 bg-zinc-900/70 px-4 py-3 text-center text-xs text-zinc-500">
            <span>Made by</span>
            <span className="font-semibold text-zinc-300">Aryan Kacker</span>
            <a href="https://www.linkedin.com/in/aryan-kacker/" target="_blank" rel="noreferrer"
              className="text-zinc-400 hover:text-emerald-300" title="Aryan Kacker on LinkedIn">
              <LinkedInIcon />
            </a>
            <span>and</span>
            <span className="font-semibold text-zinc-300">Prathu Vijayvargiya</span>
            <a href="https://www.linkedin.com/in/prathu-vijayvargiya/" target="_blank" rel="noreferrer"
              className="text-zinc-400 hover:text-emerald-300" title="Prathu Vijayvargiya on LinkedIn">
              <LinkedInIcon />
            </a>
          </div>
        </footer>
      </body>
    </html>
  );
}
