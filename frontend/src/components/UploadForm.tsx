// Purpose: File drop zone component with drag-and-drop support, visual feedback, and file selection.
// Future TODOs: Add file upload progress bar animations, drag-over image preview rendering, and format verification checks.

import React, { useState, useRef } from 'react';
import { Upload, FileText, AlertCircle } from 'lucide-react';

interface UploadFormProps {
  onUpload: (file: File) => void;
  disabled?: boolean;
}

export const UploadForm: React.FC<UploadFormProps> = ({ onUpload, disabled = false }) => {
  const [isDragActive, setIsDragActive] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === 'dragenter' || e.type === 'dragover') {
      setIsDragActive(true);
    } else if (e.type === 'dragleave') {
      setIsDragActive(false);
    }
  };

  const validateAndSetFile = (file: File | null) => {
    if (!file) return;
    if (file.type !== 'application/pdf') {
      setErrorMessage('Only PDF documents are supported currently.');
      setSelectedFile(null);
      return;
    }
    setErrorMessage(null);
    setSelectedFile(file);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragActive(false);
    
    if (disabled) return;
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      validateAndSetFile(e.dataTransfer.files[0]);
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      validateAndSetFile(e.target.files[0]);
    }
  };

  const onButtonClick = () => {
    fileInputRef.current?.click();
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (selectedFile && !disabled) {
      onUpload(selectedFile);
    }
  };

  return (
    <div className="w-full max-w-2xl mx-auto">
      <form onSubmit={handleSubmit} className="space-y-4">
        <div
          onDragEnter={handleDrag}
          onDragOver={handleDrag}
          onDragLeave={handleDrag}
          onDrop={handleDrop}
          onClick={onButtonClick}
          className={`glass-panel rounded-2xl p-8 border-2 border-dashed text-center cursor-pointer transition-all duration-300 flex flex-col items-center justify-center min-h-[220px] ${
            isDragActive 
              ? 'border-indigo-500 bg-indigo-500/10 glow-indigo' 
              : 'border-slate-800 hover:border-slate-700 hover:bg-slate-900/40'
          } ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
        >
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            accept=".pdf,application/pdf"
            onChange={handleFileChange}
            disabled={disabled}
          />
          
          {selectedFile ? (
            <div className="space-y-3 animate-fade-in">
              <div className="p-4 bg-indigo-500/10 rounded-full inline-flex text-indigo-400">
                <FileText size={40} />
              </div>
              <div>
                <p className="text-slate-200 font-semibold text-lg max-w-md truncate mx-auto">
                  {selectedFile.name}
                </p>
                <p className="text-slate-400 text-sm">
                  {(selectedFile.size / (1024 * 1024)).toFixed(2)} MB
                </p>
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="p-4 bg-slate-900/60 rounded-full inline-flex text-slate-400">
                <Upload size={32} />
              </div>
              <div>
                <p className="text-slate-200 font-medium">
                  Drag and drop your PDF here, or <span className="text-indigo-400 font-semibold hover:underline">browse files</span>
                </p>
                <p className="text-slate-500 text-xs mt-1">
                  Supports multi-page scanned PDF documents
                </p>
              </div>
            </div>
          )}
        </div>

        {errorMessage && (
          <div className="flex items-center gap-2 text-rose-400 bg-rose-950/20 border border-rose-900/50 p-3 rounded-xl text-sm">
            <AlertCircle size={18} />
            <span>{errorMessage}</span>
          </div>
        )}

        {selectedFile && !disabled && (
          <button
            type="submit"
            className="w-full py-3 px-6 rounded-xl bg-gradient-to-r from-indigo-600 to-indigo-500 hover:from-indigo-500 hover:to-indigo-400 text-white font-semibold shadow-lg hover:shadow-indigo-500/25 transition-all duration-300"
          >
            Start OCR Analysis
          </button>
        )}
      </form>
    </div>
  );
};
