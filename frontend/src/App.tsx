// Purpose: Main React Application component integrating upload form, status boards, visual layout canvases, and results.
// Future TODOs: Add react router routes, support multi-document queue listings, and add authentication scopes.

import React, { useState, useEffect } from 'react';
import { AppProvider, useAppStore } from './store';
import { UploadForm } from './components/UploadForm';
import { JobStatus } from './components/JobStatus';
import { PagePreview } from './components/PagePreview';
import { ResultViewer } from './components/ResultViewer';
import { DocumentList } from './components/DocumentList';
import { api } from './api/client';
import { RotateCcw, AlertTriangle, Layers, Loader2} from 'lucide-react';

const OCRDashboard: React.FC = () => {
  const { state, setCurrentJob, updateJobStatus, updateOcrResult, setProcessing, resetState } = useAppStore();
  const [mockMarkdown, setMockMarkdown] = useState<string>('');
  const [uploading, setUploading] = useState(false);
  const [documentListRefreshToken, setDocumentListRefreshToken] = useState(0);

  // Poll status when a job is active
  useEffect(() => {
    if (!state.currentJobId || !state.isProcessing) return;

    let intervalId: number;

    const pollStatus = async () => {
      try {
        const status = await api.getJobStatus(state.currentJobId!);
        updateJobStatus(status);

        if (status.status === 'done') {
          setProcessing(false);
          // Fetch final results
          const resultJson = await api.getJobResultJson(state.currentJobId!);
          const resultMd = await api.getJobResultMarkdown(state.currentJobId!);
          updateOcrResult(resultJson);
          setMockMarkdown(resultMd.markdown);
        } else if (status.status === 'failed') {
          setProcessing(false);
        }
      } catch (err) {
        console.error('Failed checking job status:', err);
      }
    };

    // First check
    pollStatus();
    
    // Poll every 3 seconds
    intervalId = window.setInterval(pollStatus, 3000);

    return () => clearInterval(intervalId);
  }, [state.currentJobId, state.isProcessing]);

  const handleUpload = async (file: File) => {
    setUploading(true);
    resetState();
    try {
      const response = await api.uploadDocument(file);
      setCurrentJob(response.job_id, response.document_id);
      setProcessing(true);
      setDocumentListRefreshToken((t) => t + 1);
    } catch (err) {
      console.error('Upload failed:', err);
    } finally {
      setUploading(false);
    }
  };

  const handleViewDocument = async (jobId: string, documentId: string) => {
    resetState();
    setCurrentJob(jobId, documentId);
    setProcessing(false);
    try {
      const [status, resultJson, resultMd] = await Promise.all([
        api.getJobStatus(jobId),
        api.getJobResultJson(jobId),
        api.getJobResultMarkdown(jobId),
      ]);
      updateJobStatus(status);
      updateOcrResult(resultJson);
      setMockMarkdown(resultMd.markdown);
    } catch (err) {
      console.error('Failed to load document results:', err);
    }
  };

  const handleReprocess = async () => {
    if (!state.currentJobId) return;
    setProcessing(true);
    try {
      const response = await api.reprocessJob(state.currentJobId);
      setCurrentJob(response.job_id, response.document_id);
    } catch (err) {
      console.error('Reprocess request failed:', err);
      setProcessing(false);
    }
  };

  return (
    <div className="flex flex-col min-h-screen">
      {/* Premium Header */}
      <header className="border-b border-slate-800 bg-[#0f172a]/40 backdrop-blur-md sticky top-0 z-50 px-6 py-4">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="h-10 w-10 rounded-xl bg-gradient-to-tr from-indigo-600 to-cyan-500 flex items-center justify-center font-bold text-lg text-white shadow-lg shadow-indigo-500/20">
              DS
            </div>
            <div>
              <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
                DocScribe
              </h1>
              <p className="text-xs text-slate-400">Multilingual OCR & Layout-Aware Core Pipeline</p>
            </div>
          </div>

          <div className="flex items-center gap-6">
            <div className="hidden sm:flex items-center gap-4 text-xs">
              <span className="flex items-center gap-1.5 text-slate-400">
                <Layers size={14} className="text-cyan-400" />
                Engines: <span className="font-semibold text-slate-200">Paddle + Surya</span>
              </span>
            </div>
            {state.ocrResult && (
              <button
                onClick={resetState}
                className="flex items-center gap-2 text-xs font-semibold px-4 py-2 rounded-xl bg-slate-900 border border-slate-800 text-slate-400 hover:text-slate-200 hover:border-slate-700 transition-all duration-200"
              >
                <RotateCcw size={14} />
                Upload New
              </button>
            )}
          </div>
        </div>
      </header>

      {/* Main Workspace Dashboard */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-6 flex flex-col justify-center">
        {!state.currentJobId ? (
          // Upload Page (Idle State)
          <div className="py-12 space-y-8 text-center">
            <div className="space-y-3 max-w-xl mx-auto">
              <h2 className="text-4xl font-extrabold tracking-tight text-white">
                Decipher Complex Layouts <br />
                <span className="gradient-text font-black">Across Multiple Languages</span>
              </h2>
              <p className="text-slate-400 text-sm">
                DocScribe analyzes PDFs using cheap baseline engines and automatically escalates complex structures, tables, or noisy blocks to advanced VLMs.
              </p>
            </div>
            <UploadForm onUpload={handleUpload} disabled={uploading} />
            <DocumentList onViewDocument={handleViewDocument} refreshToken={documentListRefreshToken} />
          </div>
        ) : state.isProcessing ? (
          // Progress Page (Processing State)
          <div className="py-12">
            <JobStatus status={state.jobStatus} />
          </div>
        ) : state.jobStatus?.status === 'failed' ? (
          // Failed Job State
          <div className="max-w-md mx-auto text-center space-y-4 py-12">
            <div className="p-4 bg-rose-500/10 rounded-full inline-flex text-rose-400 border border-rose-500/20">
              <AlertTriangle size={40} />
            </div>
            <h3 className="text-xl font-bold text-slate-200">OCR Extraction Failed</h3>
            <p className="text-slate-400 text-sm">
              {state.jobStatus?.error_message
                ? state.jobStatus.error_message
                : 'The processing pipeline encountered an unrecoverable failure during the execution step.'}
            </p>
            <div className="flex gap-4 justify-center">
              <button
                onClick={handleReprocess}
                className="py-2.5 px-6 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold shadow-lg text-sm transition-all duration-200"
              >
                Retry Reprocessing
              </button>
              <button
                onClick={resetState}
                className="py-2.5 px-6 rounded-xl bg-slate-900 border border-slate-800 text-slate-400 hover:text-slate-200 text-sm transition-all duration-200"
              >
                Go Back
              </button>
            </div>
          </div>
        ) : state.ocrResult ? (
          // Results Workspace (Done State)
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-stretch flex-1">
            {/* Left Hand: Canvas Layout Visualizer */}
            <PagePreview
              imagePreviewUrl={`http://localhost:9000/docscribe-storage/results/${state.currentJobId}/page_${state.selectedPageNum}.png`}
              blocks={state.ocrResult.pages[state.selectedPageNum - 1]?.blocks || []}
              pageNumber={state.selectedPageNum}
            />

            {/* Right Hand: Structured Output Viewers */}
            <ResultViewer result={state.ocrResult} markdownContent={mockMarkdown} />
          </div>
        ) : (
          <div className="text-center text-slate-500 py-12">
            <Loader2 size={32} className="animate-spin mx-auto text-indigo-400" />
            <p className="text-sm mt-2">Loading workspace details...</p>
          </div>
        )}
      </main>
    </div>
  );
};

const App: React.FC = () => {
  return (
    <AppProvider>
      <OCRDashboard />
    </AppProvider>
  );
};

export default App;
