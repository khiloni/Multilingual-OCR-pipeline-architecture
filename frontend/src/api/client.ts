// Purpose: Axios HTTP client configuration and typed API endpoints for uploading, polling jobs, and retrieval.
// Future TODOs: Configure automatic retry policies on connection drops, and add request timeout abort controllers.

import axios from 'axios';

// Base API Configuration
const API_BASE_URL = '/api/v1';

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// TypeScript interfaces corresponding to backend Pydantic schemas

export interface JobCreateResponse {
  job_id: string;
  document_id: string;
  status: 'queued' | 'processing' | 'done' | 'failed';
  message: string;
}

export interface JobStatusResponse {
  job_id: string;
  document_id: string;
  status: 'queued' | 'processing' | 'done' | 'failed';
  avg_confidence: number | null;
  started_at: string;
  completed_at: string | null;
}

export interface Block {
  block_id: string;
  type: 'heading' | 'paragraph' | 'table' | 'list' | 'caption';
  text: string;
  bbox: [number, number, number, number];
  confidence: number;
  language: string | null;
  engine_used: 'paddleocr' | 'surya';
}

export interface Page {
  page_number: number;
  language_detected: string[];
  blocks: Block[];
}

export interface DocumentMetadata {
  processed_at: string;
  avg_confidence: number;
  low_confidence_pages: number[];
}

export interface CommonOutputSchema {
  document_id: string;
  filename: string;
  page_count: number;
  pages: Page[];
  metadata: DocumentMetadata;
}

export interface DocumentListResponse {
  id: string;
  filename: string;
  storage_path: string;
  page_count: number;
  uploaded_at: string;
}

// API Methods
export const api = {
  /**
   * Upload PDF file and trigger processing job
   */
  uploadDocument: async (file: File): Promise<JobCreateResponse> => {
    const formData = new FormData();
    formData.append('file', file);
    const response = await apiClient.post<JobCreateResponse>('/jobs', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    });
    return response.data;
  },

  /**
   * Check job status
   */
  getJobStatus: async (jobId: string): Promise<JobStatusResponse> => {
    const response = await apiClient.get<JobStatusResponse>(`/jobs/${jobId}`);
    return response.data;
  },

  /**
   * Fetch structured results as JSON schema or raw Markdown
   */
  getJobResultJson: async (jobId: string): Promise<CommonOutputSchema> => {
    const response = await apiClient.get<CommonOutputSchema>(`/jobs/${jobId}/result`, {
      params: { format: 'json' },
    });
    return response.data;
  },

  getJobResultMarkdown: async (jobId: string): Promise<{ markdown: string }> => {
    const response = await apiClient.get<{ markdown: string }>(`/jobs/${jobId}/result`, {
      params: { format: 'markdown' },
    });
    return response.data;
  },

  /**
   * Get single page layout structure and image preview URL
   */
  getPageDetails: async (jobId: string, pageNum: number): Promise<{
    job_id: string;
    page_number: number;
    image_preview_url: string;
    blocks: Block[];
  }> => {
    const response = await apiClient.get(`/jobs/${jobId}/pages/${pageNum}`);
    return response.data;
  },

  /**
   * Reprocess completed or failed job using alternate engine parameters
   */
  reprocessJob: async (jobId: string, forceEngine?: 'paddleocr' | 'surya'): Promise<JobCreateResponse> => {
    const response = await apiClient.post<JobCreateResponse>(`/jobs/${jobId}/reprocess`, null, {
      params: forceEngine ? { force_engine: forceEngine } : {},
    });
    return response.data;
  },

  /**
   * List previously uploaded document histories
   */
  listDocuments: async (limit = 10, offset = 0): Promise<DocumentListResponse[]> => {
    const response = await apiClient.get<DocumentListResponse[]>('/documents', {
      params: { limit, offset },
    });
    return response.data;
  },

  /**
   * Purge a document and its jobs from records
   */
  deleteDocument: async (documentId: string): Promise<void> => {
    await apiClient.delete(`/documents/${documentId}`);
  },
};
