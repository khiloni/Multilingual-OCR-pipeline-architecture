// Purpose: Output viewer component with copy controls and toggle tabs between structured JSON and formatted Markdown.
// Future TODOs: Render interactive Markdown nodes, support syntax highlighting for JSON, and add download as file buttons.

import React, { useState } from 'react';
import { CommonOutputSchema } from '../api/client';
import { FileCode, FileJson, Copy, Check, Download } from 'lucide-react';

interface ResultViewerProps {
  result: CommonOutputSchema;
  markdownContent: string;
}

export const ResultViewer: React.FC<ResultViewerProps> = ({ result, markdownContent }) => {
  const [activeTab, setActiveTab] = useState<'markdown' | 'json'>('markdown');
  const [copied, setCopied] = useState(false);

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
