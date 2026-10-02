// Purpose: Full-text search box (Phase 2 item 6) — queries GET /search?q=
// and lists document/page/snippet results, debounced as the user types.

import React, { useEffect, useRef, useState } from 'react';
import { api, SearchResult } from '../api/client';
import { Search, Loader2, FileText } from 'lucide-react';

const DEBOUNCE_MS = 350;

// Matches the backend's StartSel/StopSel in search.py — the snippet is raw
// OCR'd document text (not trusted HTML), so matches are split out here and
// rendered as plain text + a React element, never dangerouslySetInnerHTML.
const SNIPPET_START = '\x01';
const SNIPPET_STOP = '\x02';

function renderSnippet(snippet: string): React.ReactNode {
  const parts = snippet.split(SNIPPET_START);
  return parts.map((part, i) => {
    const [matched, ...rest] = part.split(SNIPPET_STOP);
    const remainder = rest.join(SNIPPET_STOP);
    if (i === 0) return <React.Fragment key={i}>{part}</React.Fragment>;
    return (
      <React.Fragment key={i}>
        <b className="text-indigo-300 font-semibold">{matched}</b>
        {remainder}
      </React.Fragment>
    );
  });
}

export const SearchBox: React.FC = () => {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const debounceRef = useRef<number>();

  useEffect(() => {
    window.clearTimeout(debounceRef.current);
    if (!query.trim()) {
      setResults([]);
      setSearched(false);
      return;
    }
    debounceRef.current = window.setTimeout(async () => {
      setLoading(true);
      try {
        const data = await api.searchPages(query.trim());
        setResults(data);
      } catch (err) {
        console.error('Search failed:', err);
        setResults([]);
      } finally {
        setLoading(false);
        setSearched(true);
      }
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(debounceRef.current);
  }, [query]);

  return (
    <div className="max-w-xl mx-auto w-full space-y-3">
      <div className="relative">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search extracted text across all documents…"
          className="w-full pl-9 pr-9 py-2.5 rounded-xl bg-slate-900 border border-slate-800 text-sm text-slate-200 placeholder:text-slate-500 focus:outline-none focus:border-indigo-500/50"
        />
        {loading && (
          <Loader2 size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 animate-spin" />
        )}
      </div>

      {searched && !loading && (
        <div className="space-y-2 text-left">
          {results.length === 0 ? (
            <p className="text-xs text-slate-500 text-center">No matches found.</p>
          ) : (
            results.map((r) => (
              <div
                key={`${r.job_id}-${r.page_number}`}
                className="px-3 py-2.5 rounded-lg bg-slate-900/60 border border-slate-800 text-sm"
              >
                <div className="flex items-center gap-1.5 text-xs text-slate-400 mb-1">
                  <FileText size={12} />
                  <span className="font-medium text-slate-300 truncate">{r.filename}</span>
                  <span>· page {r.page_number}</span>
                </div>
                <p className="text-xs text-slate-400">{renderSnippet(r.snippet)}</p>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
};
