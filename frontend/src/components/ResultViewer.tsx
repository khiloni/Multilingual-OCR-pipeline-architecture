// Purpose: Output viewer component with copy controls and toggle tabs between structured JSON and formatted Markdown.
// Future TODOs: Render interactive Markdown nodes, support syntax highlighting for JSON, and add download as file buttons.

import React, { useState } from 'react';
import { CommonOutputSchema, api } from '../api/client';
import { FileCode, FileJson, Copy, Check, Download, Loader2 } from 'lucide-react';

interface ResultViewerProps {
  result: CommonOutputSchema;
  markdownContent: string;
  txtContent: string;
  jobId: string;
}

type PdfVariant = 'searchable' | 'highlighted' | 'structured';

const PDF_DOWNLOADERS: Record<PdfVariant, (jobId: string) => Promise<Blob>> = {
  searchable: api.downloadSearchablePdf,
  highlighted: api.downloadHighlightedPdf,
  structured: api.downloadStructuredPdf,
};

export const ResultViewer: React.FC<ResultViewerProps> = ({ result, markdownContent, txtContent, jobId }) => {
  const [activeTab, setActiveTab] = useState<'markdown' | 'json'>('markdown');
  const [copied, setCopied] = useState(false);
  const [downloadingPdf, setDownloadingPdf] = useState<PdfVariant | null>(null);

  const handleCopy = () => {
    const textToCopy = activeTab === 'markdown' 
      ? markdownContent 
      : JSON.stringify(result, null, 2);
    
    navigator.clipboard.writeText(textToCopy);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const downloadFile = (content: string, extension: string) => {
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.setAttribute('download', `extracted_result.${extension}`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  const handleDownloadJson = () => downloadFile(JSON.stringify(result, null, 2), 'json');
  const handleDownloadMarkdown = () => downloadFile(markdownContent, 'md');
  const handleDownloadTxt = () => downloadFile(txtContent, 'txt');

  const handleDownloadPdfVariant = async (variant: PdfVariant) => {
    setDownloadingPdf(variant);
    try {
      const blob = await PDF_DOWNLOADERS[variant](jobId);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `${variant}_result.pdf`);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error(`Failed to download ${variant} PDF:`, err);
    } finally {
      setDownloadingPdf(null);
    }
  };

  return (
    <div className="flex flex-col h-full space-y-4">
      <div className="flex items-center justify-between">
        {/* Toggle tabs */}
        <div className="flex bg-slate-900 border border-slate-800 p-1 rounded-xl">
          <button
            onClick={() => setActiveTab('markdown')}
            className={`flex items-center gap-2 px-4 py-1.5 rounded-lg text-xs font-semibold transition-all duration-200 ${
              activeTab === 'markdown'
                ? 'bg-indigo-600 text-white shadow-md'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <FileCode size={14} />
            Markdown
          </button>
          <button
            onClick={() => setActiveTab('json')}
            className={`flex items-center gap-2 px-4 py-1.5 rounded-lg text-xs font-semibold transition-all duration-200 ${
              activeTab === 'json'
                ? 'bg-indigo-600 text-white shadow-md'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <FileJson size={14} />
            Structured JSON
          </button>
        </div>

        {/* Action Controls */}
        <div className="flex gap-2">
          <button
            onClick={handleCopy}
            title="Copy to clipboard"
            className="p-2 rounded-xl bg-slate-900 border border-slate-800 text-slate-400 hover:text-slate-200 hover:border-slate-700 transition-all duration-200"
          >
            {copied ? <Check size={16} className="text-emerald-400" /> : <Copy size={16} />}
          </button>
          <button
            onClick={handleDownloadJson}
            title="Download JSON"
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-slate-900 border border-slate-800 text-slate-400 hover:text-slate-200 hover:border-slate-700 transition-all duration-200 text-xs font-semibold"
          >
            <Download size={14} />
            JSON
          </button>
          <button
            onClick={handleDownloadMarkdown}
            title="Download Markdown"
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-slate-900 border border-slate-800 text-slate-400 hover:text-slate-200 hover:border-slate-700 transition-all duration-200 text-xs font-semibold"
          >
            <Download size={14} />
            Markdown
          </button>
          <button
            onClick={handleDownloadTxt}
            title="Download TXT"
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-slate-900 border border-slate-800 text-slate-400 hover:text-slate-200 hover:border-slate-700 transition-all duration-200 text-xs font-semibold"
          >
            <Download size={14} />
            TXT
          </button>
          <button
            onClick={() => handleDownloadPdfVariant('searchable')}
            disabled={downloadingPdf !== null}
            title="Download Searchable PDF"
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-slate-900 border border-slate-800 text-slate-400 hover:text-slate-200 hover:border-slate-700 transition-all duration-200 text-xs font-semibold disabled:opacity-50"
          >
            {downloadingPdf === 'searchable' ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
            Searchable PDF
          </button>
          <button
            onClick={() => handleDownloadPdfVariant('highlighted')}
            disabled={downloadingPdf !== null}
            title="Download Language-Highlighted PDF"
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-slate-900 border border-slate-800 text-slate-400 hover:text-slate-200 hover:border-slate-700 transition-all duration-200 text-xs font-semibold disabled:opacity-50"
          >
            {downloadingPdf === 'highlighted' ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
            Highlighted PDF
          </button>
          <button
            onClick={() => handleDownloadPdfVariant('structured')}
            disabled={downloadingPdf !== null}
            title="Download Structured Reconstruction PDF"
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-slate-900 border border-slate-800 text-slate-400 hover:text-slate-200 hover:border-slate-700 transition-all duration-200 text-xs font-semibold disabled:opacity-50"
          >
            {downloadingPdf === 'structured' ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
            Structured PDF
          </button>
        </div>
      </div>

      {/* Content box */}
      <div className="flex-1 glass-panel rounded-2xl p-4 border border-slate-800 relative overflow-hidden flex flex-col min-h-[450px]">
        {activeTab === 'markdown' ? (
          <textarea
            readOnly
            value={markdownContent}
            className="flex-1 w-full h-full bg-transparent border-0 resize-none text-slate-300 font-mono text-sm leading-relaxed focus:ring-0 outline-none overflow-y-auto"
          />
        ) : (
          <pre className="flex-1 overflow-auto text-slate-300 font-mono text-xs leading-relaxed max-w-full p-2">
            <code>{JSON.stringify(result, null, 2)}</code>
          </pre>
        )}
      </div>
    </div>
  );
};
