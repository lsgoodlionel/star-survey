import { z } from 'zod';

export const meSchema = z.object({
  tenantId: z.string().min(1),
  actorId: z.string().min(1),
  roles: z.array(z.string()),
});

export const tokenViewSchema = z.object({
  accessToken: z.string().min(1),
  tokenType: z.literal('Bearer'),
  expiresIn: z.number().positive(),
  principalId: z.string().min(1),
  tenantId: z.string().min(1),
});

export type Me = z.infer<typeof meSchema>;
export type TokenView = z.infer<typeof tokenViewSchema>;
