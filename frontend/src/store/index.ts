// Purpose: Application state management using React hooks context to store processing job history and current select IDs.
// Future TODOs: Integrate Zustand or Redux Toolkits if complex workspace states expand in later stages.

import React, { createContext, useContext, useState, ReactNode } from 'react';
import { JobStatusResponse, CommonOutputSchema } from '../api/client';

interface AppState {
  currentJobId: string | null;
  currentDocumentId: string | null;
  jobStatus: JobStatusResponse | null;
  ocrResult: CommonOutputSchema | null;
  selectedPageNum: number;
  isProcessing: boolean;
  historyJobs: string[];
}

interface AppContextType {
  state: AppState;
  setCurrentJob: (jobId: string, docId: string) => void;
  updateJobStatus: (status: JobStatusResponse | null) => void;
  updateOcrResult: (result: CommonOutputSchema | null) => void;
  setSelectedPage: (pageNum: number) => void;
  setProcessing: (processing: boolean) => void;
  addJobToHistory: (jobId: string) => void;
  resetState: () => void;
}

const defaultState: AppState = {
  currentJobId: null,
  currentDocumentId: null,
  jobStatus: null,
  ocrResult: null,
  selectedPageNum: 1,
  isProcessing: false,
  historyJobs: [],
};

const AppContext = createContext<AppContextType | undefined>(undefined);

export const AppProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [state, setState] = useState<AppState>(defaultState);

  const setCurrentJob = (jobId: string, docId: string) => {
    setState((prev) => ({
      ...prev,
      currentJobId: jobId,
      currentDocumentId: docId,
      selectedPageNum: 1,
      ocrResult: null,
    }));
  };

  const updateJobStatus = (jobStatus: JobStatusResponse | null) => {
    setState((prev) => ({ ...prev, jobStatus }));
  };

  const updateOcrResult = (ocrResult: CommonOutputSchema | null) => {
    setState((prev) => ({ ...prev, ocrResult }));
  };

  const setSelectedPage = (selectedPageNum: number) => {
    setState((prev) => ({ ...prev, selectedPageNum }));
  };

  const setProcessing = (isProcessing: boolean) => {
    setState((prev) => ({ ...prev, isProcessing }));
  };

  const addJobToHistory = (jobId: string) => {
    setState((prev) => {
      if (prev.historyJobs.includes(jobId)) return prev;
      return { ...prev, historyJobs: [...prev.historyJobs, jobId] };
    });
  };

  const resetState = () => {
    setState((prev) => ({
      ...defaultState,
      historyJobs: prev.historyJobs,
    }));
  };

  return (
    <AppContext.Provider
      value={{
        state,
        setCurrentJob,
        updateJobStatus,
        updateOcrResult,
        setSelectedPage,
        setProcessing,
        addJobToHistory,
        resetState,
      }}
    >
      {children}
    </AppContext.Provider>
  );
};

export const useAppStore = () => {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error('useAppStore must be used within an AppProvider');
  }
  return context;
};
