// Purpose: Processing indicator displaying active stages, progress bars, and execution details for background task.
// Future TODOs: Hook up real-time WebSocket listeners, show engine telemetry metrics, and add error debug consoles.

import React from 'react';
import { Loader2, CheckCircle2, XCircle, FileClock } from 'lucide-react';
import { JobStatusResponse } from '../api/client';

interface JobStatusProps {
  status: JobStatusResponse | null;
  onCancel?: () => void;
}

export const JobStatus: React.FC<JobStatusProps> = ({ status }) => {
  if (!status) return null;

  const steps = [
    { name: 'Upload & Queued', key: 'queued', label: 'Document stored in Object Storage' },
    { name: 'Rasterization', key: 'rasterizing', label: 'Converting PDF pages into images' },
    { name: 'OCR Processing', key: 'processing', label: 'Extracting text (PaddleOCR with Surya fallback)' },
    { name: 'Format Normalization', key: 'normalizing', label: 'Compiling structured outputs' },
  ];

  const getStepStatus = (stepKey: string) => {
    if (status.status === 'failed') return 'failed';
    if (status.status === 'done') return 'completed';
    
    // Simple state mapping for mock/skeleton
    if (status.status === 'queued' && stepKey === 'queued') return 'active';
    if (status.status === 'processing') {
      if (stepKey === 'queued') return 'completed';
      if (stepKey === 'rasterizing' || stepKey === 'processing') return 'active';
    }
    return 'pending';
  };

  return (
    <div className="w-full max-w-2xl mx-auto glass-panel rounded-2xl p-6 border border-slate-800 glow-indigo">
      <div className="flex items-center justify-between border-b border-slate-800 pb-4 mb-6">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-indigo-500/10 rounded-lg text-indigo-400">
            <FileClock size={20} />
          </div>
          <div>
            <h3 className="font-semibold text-slate-200">Processing Document</h3>
            <p className="text-slate-500 text-xs truncate max-w-xs">Job ID: {status.job_id}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {status.status === 'processing' || status.status === 'queued' ? (
            <span className="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
              <Loader2 size={12} className="animate-spin" />
              Active
            </span>
          ) : status.status === 'done' ? (
            <span className="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              <CheckCircle2 size={12} />
              Completed
            </span>
          ) : (
            <span className="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-semibold bg-rose-500/10 text-rose-400 border border-rose-500/20">
              <XCircle size={12} />
              Failed
            </span>
          )}
        </div>
      </div>

      <div className="space-y-6">
        {steps.map((step, idx) => {
          const stepStatus = getStepStatus(step.key);
          
          return (
            <div key={step.key} className="flex gap-4 items-start relative">
              {idx < steps.length - 1 && (
                <div className="absolute left-[15px] top-[30px] bottom-[-22px] w-[2px] bg-slate-800" />
              )}
              
              <div className="z-10 mt-1">
                {stepStatus === 'completed' ? (
                  <div className="w-8 h-8 rounded-full bg-emerald-500/20 text-emerald-400 border border-emerald-500/50 flex items-center justify-center font-bold text-sm">
                    ✓
                  </div>
                ) : stepStatus === 'active' ? (
                  <div className="w-8 h-8 rounded-full bg-indigo-500/20 text-indigo-400 border border-indigo-500 flex items-center justify-center font-bold text-sm animate-pulse">
                    <Loader2 size={16} className="animate-spin" />
                  </div>
                ) : stepStatus === 'failed' ? (
                  <div className="w-8 h-8 rounded-full bg-rose-500/20 text-rose-400 border border-rose-500 flex items-center justify-center font-bold text-sm">
                    ✕
                  </div>
                ) : (
                  <div className="w-8 h-8 rounded-full bg-slate-900 border border-slate-800 text-slate-500 flex items-center justify-center font-bold text-sm">
                    {idx + 1}
                  </div>
                )}
              </div>

              <div className="flex-1">
                <h4 className={`font-semibold text-sm ${stepStatus === 'active' ? 'text-indigo-400' : stepStatus === 'pending' ? 'text-slate-500' : 'text-slate-300'}`}>
                  {step.name}
                </h4>
                <p className="text-slate-500 text-xs mt-0.5">{step.label}</p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
