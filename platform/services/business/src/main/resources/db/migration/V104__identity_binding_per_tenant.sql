-- 身份绑定改为按租户唯一：(租户, 提供方, 企业/应用标识, 外部标识)。
--
-- 原先三元组全平台唯一，有两个问题：
--   1. 业务上错误：平台共用的小程序、公众号下，同一个 openid 完全可能同时是两个租户的参与者，
--      全局唯一会让第二个租户无法绑定此人；
--   2. 跨租户信息泄露：B 绑定一个已被 A 绑定的身份会收到 409，等于告诉 B"此人在别的租户存在"。
-- 按租户唯一后，同一外部身份在不同租户下是互不相关的主体；openid 作用域陷阱仍由三元组本身规避。
ALTER TABLE identity_binding DROP CONSTRAINT identity_binding_identity_key;
ALTER TABLE identity_binding
    ADD CONSTRAINT identity_binding_tenant_identity_key UNIQUE (tenant_id, provider, app_id, external_id);
