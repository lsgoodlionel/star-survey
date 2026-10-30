import { z } from 'zod';
import { ApiError, apiErrorForStatus, unavailableApiError, unexpectedApiError } from './errors';
import { withRequestTimeout } from './http';

export const exportFormatSchema = z.enum(['csv', 'xlsx', 'sav', 'docx', 'pdf', 'report-docx', 'attachments']);
export const exportStatusSchema = z.enum([
  'queued',
  'running',
  'completed',
  'failed',
  'cancelled',
  'expired',
]);
export const exportFilterSchema = z.object({
  states: z.array(z.enum(['in_progress', 'engine_completed', 'deleted'])).nullable(),
  versions: z.array(z.number().int().positive()).nullable(),
});

export const exportJobSchema = z.object({
  jobId: z.string().uuid(),
  surveyId: z.string().uuid(),
  format: exportFormatSchema,
  templateVersion: z.string().min(1),
  filter: exportFilterSchema,
  status: exportStatusSchema,
  sensitiveRevealed: z.boolean(),
  totalRows: z.number().int().nonnegative().nullable(),
  processedRows: z.number().int().nonnegative(),
  attempts: z.number().int().nonnegative(),
  error: z.string().nullable(),
  fileSize: z.number().int().nonnegative().nullable(),
  sha256: z.string().regex(/^[a-f0-9]{64}$/i).nullable(),
  createdAt: z.string().datetime({ offset: true }),
  startedAt: z.string().datetime({ offset: true }).nullable(),
  finishedAt: z.string().datetime({ offset: true }).nullable(),
  expiresAt: z.string().datetime({ offset: true }).nullable(),
});

export const wordTemplateSchema = z.object({
  id: z.string().uuid(),
  name: z.string().min(1).max(200),
  version: z.number().int().positive(),
  sha256: z.string().regex(/^[a-f0-9]{64}$/i),
  createdAt: z.string().datetime({ offset: true }).optional(),
});
export const wordTemplateListSchema = z.array(wordTemplateSchema);

export type ExportFormat = z.infer<typeof exportFormatSchema>;
export type ExportStatus = z.infer<typeof exportStatusSchema>;
export type ExportJobView = z.infer<typeof exportJobSchema>;
export type ExportFilter = z.infer<typeof exportFilterSchema>;
export type WordTemplateView = z.infer<typeof wordTemplateSchema>;

export interface CreateExportInput {
  format: ExportFormat;
  filter?: ExportFilter | null;
  templateVersion?: string | null;
}

export interface ExportRequestOptions {
  idempotencyKey?: string;
  signal?: AbortSignal;
}

export interface ExportDownload {
  blob: Blob;
  filename: string;
  contentType: string;
  sha256: string | null;
}

export interface ExportClientOptions {
  fetchImpl?: typeof fetch;
  getToken?: () => string | null;
  onUnauthorized?: (requestToken: string | null) => void | Promise<void>;
  timeoutMs?: number;
}

export interface ExportClient {
  create(surveyId: string, input: CreateExportInput, options?: ExportRequestOptions): Promise<ExportJobView>;
  get(jobId: string, signal?: AbortSignal): Promise<ExportJobView>;
  cancel(jobId: string, signal?: AbortSignal): Promise<ExportJobView>;
  download(jobId: string, signal?: AbortSignal): Promise<ExportDownload>;
  listWordTemplates(surveyId: string, signal?: AbortSignal): Promise<WordTemplateView[]>;
  uploadWordTemplate(surveyId: string, name: string, file: File, signal?: AbortSignal): Promise<WordTemplateView>;
  uploadWordTemplateVersion(surveyId: string, templateId: string, file: File, signal?: AbortSignal): Promise<WordTemplateView>;
}

export const exportJobQueryKey = (tenantId: string, surveyId: string, jobId: string) =>
  ['export-job', tenantId, surveyId, jobId] as const;
export const wordTemplatesQueryKey = (tenantId: string, surveyId: string) =>
  ['word-templates', tenantId, surveyId] as const;

export function createExportClient(options: ExportClientOptions = {}): ExportClient {
  const fetchImpl = options.fetchImpl ?? fetch;

  async function request<T>(
    path: string,
    init: RequestInit,
    consume: (response: Response) => Promise<T>,
  ): Promise<T> {
    const token = options.getToken?.() ?? null;
    const headers = new Headers(init.headers);
    if (token) headers.set('Authorization', `Bearer ${token}`);
    try {
      return await withRequestTimeout(
        async (signal) => {
          const response = await fetchImpl(path, {
            ...init,
            headers,
            credentials: 'same-origin',
            signal,
          });
          if (response.status === 401) await options.onUnauthorized?.(token);
          if (!response.ok) throw apiErrorForStatus(response.status, await readErrorPayload(response));
          return consume(response);
        },
        init.signal,
        options.timeoutMs,
      );
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (error instanceof DOMException && error.name === 'AbortError') throw unavailableApiError();
      if (error instanceof TypeError) throw unavailableApiError();
      throw error;
    }
  }

  async function requestJob(path: string, init: RequestInit): Promise<ExportJobView> {
    try {
      return await request(path, init, async (response) =>
        exportJobSchema.parse(await response.json()),
      );
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw unexpectedApiError();
    }
  }

  async function requestTemplate(path: string, init: RequestInit): Promise<WordTemplateView> {
    try {
      return await request(path, init, async (response) =>
        wordTemplateSchema.parse(await response.json()),
      );
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw unexpectedApiError();
    }
  }

  return {
    create: (surveyId, input, requestOptions = {}) => {
      const headers = new Headers({ Accept: 'application/json', 'Content-Type': 'application/json' });
      if (requestOptions.idempotencyKey) {
        headers.set('Idempotency-Key', requestOptions.idempotencyKey);
      }
      return requestJob(`/v1/surveys/${encodeURIComponent(surveyId)}/exports`, {
        method: 'POST',
        headers,
        body: JSON.stringify(input),
        signal: requestOptions.signal,
      });
    },
    get: (jobId, signal) =>
      requestJob(`/v1/exports/${encodeURIComponent(jobId)}`, {
        method: 'GET',
        headers: { Accept: 'application/json' },
        signal,
      }),
    cancel: (jobId, signal) =>
      requestJob(`/v1/exports/${encodeURIComponent(jobId)}/cancel`, {
        method: 'POST',
        headers: { Accept: 'application/json' },
        signal,
      }),
    download: async (jobId, signal) => {
      return request(
        `/v1/exports/${encodeURIComponent(jobId)}/download`,
        { method: 'GET', signal },
        async (response) => {
          const filename = parseDownloadFilename(response.headers.get('Content-Disposition'));
          const contentType = response.headers.get('Content-Type')?.split(';', 1)[0]?.trim();
          if (!contentType) throw new Error('导出文件响应缺少内容类型');
          const sha256 = response.headers.get('X-Content-SHA256');
          if (sha256 !== null && !/^[a-f0-9]{64}$/i.test(sha256)) {
            throw new Error('导出文件校验值无效');
          }
          return { blob: await response.blob(), filename, contentType, sha256 };
        },
      );
    },
    listWordTemplates: async (surveyId, signal) => {
      try {
        return await request(
          `/v1/surveys/${encodeURIComponent(surveyId)}/word-templates`,
          { method: 'GET', headers: { Accept: 'application/json' }, signal },
          async (response) => wordTemplateListSchema.parse(await response.json()),
        );
      } catch (error) {
        if (error instanceof ApiError) throw error;
        throw unexpectedApiError();
      }
    },
    uploadWordTemplate: (surveyId, name, file, signal) => {
      const body = new FormData();
      body.set('name', name.trim());
      body.set('file', file);
      return requestTemplate(`/v1/surveys/${encodeURIComponent(surveyId)}/word-templates`, {
        method: 'POST', headers: { Accept: 'application/json' }, body, signal,
      });
    },
    uploadWordTemplateVersion: (surveyId, templateId, file, signal) => {
      const body = new FormData();
      body.set('file', file);
      return requestTemplate(
        `/v1/surveys/${encodeURIComponent(surveyId)}/word-templates/${encodeURIComponent(templateId)}/versions`,
        { method: 'POST', headers: { Accept: 'application/json' }, body, signal },
      );
    },
  };
}

function parseDownloadFilename(header: string | null): string {
  if (!header || !/^attachment(?:;|$)/i.test(header.trim())) {
    throw new Error('导出文件响应缺少有效文件名');
  }
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header)?.[1];
  const quoted = /filename="((?:\\.|[^"])*)"/i.exec(header)?.[1];
  const plain = /filename=([^;\s]+)/i.exec(header)?.[1];
  let filename: string;
  try {
    filename = encoded ? decodeURIComponent(encoded) : (quoted?.replace(/\\(.)/g, '$1') ?? plain ?? '');
  } catch {
    throw new Error('导出文件名编码无效');
  }
  if (
    !filename ||
    filename === '.' ||
    filename === '..' ||
    /[/\\]/.test(filename) ||
    Array.from(filename).some((character) => {
      const code = character.charCodeAt(0);
      return code < 32 || code === 127;
    })
  ) {
    throw new Error('导出文件名无效');
  }
  return filename;
}

async function readErrorPayload(response: Response): Promise<unknown> {
  if (!response.headers.get('Content-Type')?.includes('application/json')) return undefined;
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}
