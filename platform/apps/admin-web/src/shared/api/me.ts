import type { ApiClient } from './http';
import { meSchema } from './schemas';

export function getMe(api: ApiClient) {
  return api.request({ path: '/v1/me', schema: meSchema });
}
