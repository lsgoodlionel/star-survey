import { z } from 'zod';

export const orgCapabilitiesSchema = z.object({ canManageSettings: z.boolean(), canManageMembers: z.boolean() });
