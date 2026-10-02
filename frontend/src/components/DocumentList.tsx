// Purpose: Document history list — status badges, per-document download/delete
// actions, click-to-view for completed documents (Phase 1 item 8).

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api, DocumentListResponse } from '../api/client';
import {
  FileText, Loader2, CheckCircle2, XCircle, Clock,
  Download, Trash2, Eye, AlertTriangle,
} from 'lucide-react';

interface DocumentListProps {
  onViewDocument: (jobId: string, documentId: string) => void;
  refreshToken?: number;
}

const STATUS_BADGE: Record<string, { label: string; className: string; icon: React.ReactNode }> = {
  queued: {
    label: 'Queued',
    className: 'bg-slate-500/10 text-slate-400 border-slate-500/20',
    icon: <Clock size={12} />,
  },
  processing: {
    label: 'Processing',
    className: 'bg-indigo-500/10 text-indigo-400 border-indigo-500/20',
    icon: <Loader2 size={12} className="animate-spin" />,
  },
  done: {
    label: 'Done',
    className: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
    icon: <CheckCircle2 size={12} />,
  },
  failed: {
    label: 'Failed',
    className: 'bg-rose-500/10 text-rose-400 border-rose-500/20',
    icon: <XCircle size={12} />,
  },
};

const POLL_INTERVAL_MS = 4000;

export const DocumentList: React.FC<DocumentListProps> = ({ onViewDocument, refreshToken }) => {
  const [documents, setDocuments] = useState<DocumentListResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const pollTimer = useRef<number | null>(null);

  const fetchDocuments = useCallback(async () => {
    try {
      const docs = await api.listDocuments(50, 0);
      setDocuments(docs);
      setError(null);
    } catch (err) {
      console.error('Failed to load documents:', err);
      setError('Could not load document list.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchDocuments();
  }, [fetchDocuments, refreshToken]);

  // Poll only while at least one document is queued/processing.
  useEffect(() => {
    const hasActive = documents.some(
      (d) => d.latest_status === 'queued' || d.latest_status === 'processing'
    );
    if (pollTimer.current) {
      window.clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
    if (hasActive) {
      pollTimer.current = window.setTimeout(fetchDocuments, POLL_INTERVAL_MS);
    }
    return () => {
      if (pollTimer.current) window.clearTimeout(pollTimer.current);
    };
  }, [documents, fetchDocuments]);

  const showToast = (message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(null), 2500);
  };

  const handleDownload = async (doc: DocumentListResponse, format: 'json' | 'markdown') => {
    if (!doc.latest_job_id || doc.latest_status !== 'done') return;
    try {
      const content =
        format === 'json'
          ? JSON.stringify(await api.getJobResultJson(doc.latest_job_id), null, 2)
          : (await api.getJobResultMarkdown(doc.latest_job_id)).markdown;
      const ext = format === 'json' ? 'json' : 'md';
      const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `${doc.filename.replace(/\.pdf$/i, '')}.${ext}`);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Download failed:', err);
      showToast('Download failed — see console for details.');
    }
  };

  const handleDeleteConfirmed = async (doc: DocumentListResponse) => {
    setDeletingId(doc.id);
    try {
      await api.deleteDocument(doc.id);
      setDocuments((prev) => prev.filter((d) => d.id !== doc.id));
      showToast(`Deleted "${doc.filename}".`);
    } catch (err) {
      console.error('Delete failed:', err);
      showToast('Delete failed — see console for details.');
    } finally {
      setDeletingId(null);
      setPendingDeleteId(null);
    }
  };

  if (loading) {
    return (
      <div className="w-full max-w-3xl mx-auto py-8 text-center text-slate-500">
        <Loader2 size={24} className="animate-spin mx-auto mb-2" />
        <p className="text-sm">Loading documents…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="w-full max-w-3xl mx-auto py-8 text-center text-rose-400 text-sm">
        {error}
      </div>
    );
  }

  if (documents.length === 0) {
    return (
      <div className="w-full max-w-3xl mx-auto py-8 text-center text-slate-500">
        <FileText size={28} className="mx-auto mb-2 opacity-50" />
        <p className="text-sm">Upload a document to get started.</p>
      </div>
    );
  }

  return (
    <div className="w-full max-w-3xl mx-auto space-y-3">
      <h3 className="text-sm font-semibold text-slate-400 px-1">Recent Documents</h3>

      {toast && (
        <div className="text-xs px-3 py-2 rounded-lg bg-slate-900 border border-slate-800 text-slate-300">
          {toast}
        </div>
      )}

      <div className="space-y-2">
        {documents.map((doc) => {
          const status = doc.latest_status ?? 'queued';
          const badge = STATUS_BADGE[status] ?? STATUS_BADGE.queued;
          const isDone = status === 'done';
          const isDeleting = deletingId === doc.id;

          return (
            <div
              key={doc.id}
              className="glass-panel rounded-xl border border-slate-800 p-3 flex items-center gap-3"
            >
              <div className="p-2 rounded-lg bg-slate-900 text-slate-500 shrink-0">
                <FileText size={18} />
              </div>

              <div
                className={`flex-1 min-w-0 ${isDone ? 'cursor-pointer' : ''}`}
                onClick={() => {
                  if (isDone && doc.latest_job_id) onViewDocument(doc.latest_job_id, doc.id);
                }}
                title={isDone ? 'View results' : undefined}
              >
                <p className="text-sm font-medium text-slate-200 truncate">{doc.filename}</p>
                <p className="text-xs text-slate-500">
                  {new Date(doc.uploaded_at).toLocaleString()} · {doc.page_count} page
                  {doc.page_count === 1 ? '' : 's'}
                  {isDone && doc.avg_confidence != null && (
                    <> · {(doc.avg_confidence * 100).toFixed(1)}% confidence</>
                  )}
                  {isDone && doc.avg_quality_score != null && (
                    <> · {(doc.avg_quality_score * 100).toFixed(1)}% quality</>
                  )}
                </p>
                {status === 'failed' && doc.error_message && (
                  <p className="text-xs text-rose-400 mt-1 flex items-center gap-1 truncate">
                    <AlertTriangle size={11} className="shrink-0" />
                    {doc.error_message}
                  </p>
                )}
              </div>

              <span
                className={`shrink-0 flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold border ${badge.className}`}
              >
                {badge.icon}
                {badge.label}
              </span>

              <div className="flex items-center gap-1 shrink-0">
                {isDone && (
                  <button
                    onClick={() => doc.latest_job_id && onViewDocument(doc.latest_job_id, doc.id)}
                    title="View"
                    className="p-2 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition-colors"
                  >
                    <Eye size={15} />
                  </button>
                )}
                <button
                  onClick={() => handleDownload(doc, 'json')}
                  disabled={!isDone}
                  title="Download JSON"
                  className="flex items-center gap-1 px-2 py-2 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition-colors disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:bg-transparent text-[11px] font-mono"
                >
                  <Download size={13} />
                  JSON
                </button>
                <button
                  onClick={() => handleDownload(doc, 'markdown')}
                  disabled={!isDone}
                  title="Download Markdown"
                  className="flex items-center gap-1 px-2 py-2 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800 transition-colors disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:bg-transparent text-[11px] font-mono"
                >
                  <Download size={13} />
                  MD
                </button>
                <button
                  onClick={() => setPendingDeleteId(doc.id)}
                  disabled={isDeleting}
                  title="Delete"
                  className="p-2 rounded-lg text-slate-400 hover:text-rose-400 hover:bg-rose-500/10 transition-colors disabled:opacity-30"
                >
                  {isDeleting ? <Loader2 size={15} className="animate-spin" /> : <Trash2 size={15} />}
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {/* Delete confirmation dialog */}
      {pendingDeleteId && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
          <div className="glass-panel rounded-2xl border border-slate-800 p-6 max-w-sm w-full space-y-4">
            <div className="flex items-center gap-3 text-rose-400">
              <AlertTriangle size={22} />
              <h4 className="font-semibold text-slate-200">Delete document?</h4>
            </div>
            <p className="text-sm text-slate-400">
              This removes the document, all its OCR results, and stored files from object
              storage. This cannot be undone.
            </p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setPendingDeleteId(null)}
                className="px-4 py-2 rounded-xl text-sm text-slate-400 hover:text-slate-200 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  const doc = documents.find((d) => d.id === pendingDeleteId);
                  if (doc) handleDeleteConfirmed(doc);
                }}
                className="px-4 py-2 rounded-xl text-sm bg-rose-600 hover:bg-rose-500 text-white font-semibold transition-colors"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
