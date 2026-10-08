export type ApiErrorKind =
  | 'unauthenticated'
  | 'forbidden'
  | 'not_found'
  | 'conflict'
  | 'validation'
  | 'pending'
  | 'unavailable'
  | 'unexpected';

export class ApiError extends Error {
  constructor(
    readonly kind: ApiErrorKind,
    message: string,
    readonly status?: number,
    readonly traceId?: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

const statusKinds: Partial<Record<number, ApiErrorKind>> = {
  202: 'pending',
  401: 'unauthenticated',
  403: 'forbidden',
  404: 'not_found',
  409: 'conflict',
  422: 'validation',
};

const kindMessages: Record<ApiErrorKind, string> = {
  unauthenticated: '登录已失效，请重新登录',
  forbidden: '无权执行当前操作',
  not_found: '资源不存在或不可访问',
  conflict: '草稿已被其他人修改，本地内容未被覆盖',
  validation: '定义或导入内容未通过校验',
  pending: '发布结果正在核对',
  unavailable: '服务暂时不可用，请稍后重试',
  unexpected: '操作失败，请稍后重试',
};

export function apiErrorForStatus(status: number, payload?: unknown): ApiError {
  const kind = statusKinds[status] ?? (status >= 500 ? 'unavailable' : 'unexpected');
  return new ApiError(kind, kindMessages[kind], status, readTraceId(payload));
}

export function unavailableApiError(): ApiError {
  return new ApiError('unavailable', kindMessages.unavailable);
}

export function unexpectedApiError(): ApiError {
  return new ApiError('unexpected', kindMessages.unexpected);
}

export function invalidApiUrlError(): ApiError {
  return new ApiError('unexpected', '请求地址无效');
}

function readTraceId(payload: unknown): string | undefined {
  if (!payload || typeof payload !== 'object') return undefined;
  const traceId = Reflect.get(payload, 'traceId');
  return typeof traceId === 'string' ? traceId : undefined;
}
