import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';
import { useAuth } from '../auth/AuthProvider';

const schema = z.object({ managePlatform: z.boolean() });
export function usePlatformCapabilities() {
  const { api, session } = useAuth();
  return useQuery({ queryKey: ['platform-capabilities', session?.me.tenantId, session?.me.actorId],
    queryFn: ({ signal }) => api.request({ path: '/v1/platform-admin/capabilities', schema, signal }),
    enabled: Boolean(session), retry: false, staleTime: 0 });
}
