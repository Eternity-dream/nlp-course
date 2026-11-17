// API 客户端封装

import type * as T from './types';

class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public retryable: boolean = false
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const data = await response.json();

  if (!response.ok) {
    const error = data.error || {};
    throw new ApiError(response.status, error.code || 'unknown', error.message || '请求失败', error.retryable);
  }

  return data;
}

export const api = {
  // 健康和配置
  async health() {
    return request<{ status: string; schema_version: number }>('/api/health');
  },

  async getRuntime() {
    return request<T.RuntimeStatus>('/api/runtime');
  },

  async getConfigStatus() {
    return request<T.ConfigStatus>('/api/config/status');
  },

  async checkService(service: 'llm' | 'search' | 'amap', destination = '西安') {
    return request<T.ServiceCheck>('/api/config/check', {
      method: 'POST',
      body: JSON.stringify({ service, destination }),
    });
  },

  async listServiceChecks() {
    return request<T.ServiceCheck[]>('/api/config/checks');
  },

  // 行程
  async createTrip(fields: T.TripFields) {
    return request<T.Trip>('/api/trips', {
      method: 'POST',
      body: JSON.stringify(fields),
    });
  },

  async listTrips(limit = 50, offset = 0) {
    return request<T.Trip[]>(`/api/trips?limit=${limit}&offset=${offset}`);
  },

  async getTrip(id: string) {
    return request<T.Trip>(`/api/trips/${id}`);
  },

  async patchTrip(id: string, patch: { expected_revision: number; [key: string]: any }) {
    return request<T.Trip>(`/api/trips/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    });
  },

  async lookupPlaces(tripId: string, query: string) {
    return request<T.Place[]>(`/api/trips/${tripId}/places?q=${encodeURIComponent(query)}`);
  },

  // 任务
  async submitJob(tripId: string, expectedRevision: number, idempotencyKey: string) {
    return request<T.PlanJob>(`/api/trips/${tripId}/jobs`, {
      method: 'POST',
      body: JSON.stringify({ expected_revision: expectedRevision, idempotency_key: idempotencyKey }),
    });
  },

  async listJobs(tripId: string) {
    return request<T.PlanJob[]>(`/api/trips/${tripId}/jobs`);
  },

  async getJob(jobId: string) {
    return request<T.PlanJob>(`/api/jobs/${jobId}`);
  },

  async retryJob(jobId: string, expectedRevision: number, idempotencyKey: string) {
    return request<T.PlanJob>(`/api/jobs/${jobId}/retry`, {
      method: 'POST',
      body: JSON.stringify({ expected_revision: expectedRevision, idempotency_key: idempotencyKey }),
    });
  },

  async resumeJob(jobId: string, expectedRevision: number) {
    return request<T.PlanJob>(`/api/jobs/${jobId}/resume`, {
      method: 'POST',
      body: JSON.stringify({ expected_revision: expectedRevision }),
    });
  },

  // 行程草案
  async getItinerary(tripId: string, revision?: number) {
    const query = revision ? `?revision=${revision}` : '';
    return request<T.Itinerary>(`/api/trips/${tripId}/itinerary${query}`);
  },

  async editItinerary(
    tripId: string,
    expectedRevision: number,
    expectedRequestRevision: number,
    idempotencyKey: string,
    operations: any[]
  ) {
    return request<T.PlanJob>(`/api/trips/${tripId}/itinerary`, {
      method: 'PATCH',
      body: JSON.stringify({
        expected_revision: expectedRevision,
        expected_request_revision: expectedRequestRevision,
        idempotency_key: idempotencyKey,
        operations,
      }),
    });
  },

  // 偏好
  async getPreferences() {
    return request<T.PreferenceProfile>('/api/preferences');
  },

  async putPreferences(expectedRevision: number, notes: string) {
    return request<T.PreferenceProfile>('/api/preferences', {
      method: 'PUT',
      body: JSON.stringify({ expected_revision: expectedRevision, notes }),
    });
  },

  // 知识库
  async createKnowledge(kind: 'text' | 'url' | 'bookmark', title: string, content: string) {
    const body: any = { kind, title };
    if (kind === 'text') {
      body.text = content;
    } else {
      body.url = content;
    }
    return request<T.KnowledgeSource>('/api/knowledge', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  },

  async uploadKnowledgeFile(title: string, file: File) {
    const formData = new FormData();
    formData.append('title', title);
    formData.append('file', file);
    const response = await fetch('/api/knowledge/files', {
      method: 'POST',
      body: formData,
    });
    if (!response.ok) {
      const data = await response.json();
      const error = data.error || {};
      throw new ApiError(response.status, error.code || 'unknown', error.message || '上传失败', error.retryable);
    }
    return response.json() as Promise<T.KnowledgeSource>;
  },

  async listKnowledge(limit = 50, offset = 0) {
    return request<T.KnowledgeSource[]>(`/api/knowledge?limit=${limit}&offset=${offset}`);
  },

  async getKnowledge(id: string) {
    return request<T.KnowledgeDetail>(`/api/knowledge/${id}`);
  },

  async searchKnowledge(query: string, limit = 8) {
    return request<T.KnowledgeHit[]>(`/api/knowledge/search?q=${encodeURIComponent(query)}&limit=${limit}`);
  },

  async retryKnowledge(id: string) {
    return request<T.KnowledgeSource>(`/api/knowledge/${id}/retry`, { method: 'POST' });
  },

  async deleteKnowledge(id: string) {
    return request<void>(`/api/knowledge/${id}`, { method: 'DELETE' });
  },

  getOriginalUrl(id: string) {
    return `/api/knowledge/${id}/original`;
  },
};

export { ApiError };
