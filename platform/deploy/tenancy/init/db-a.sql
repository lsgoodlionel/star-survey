-- 租户 A 的数据库服务器上额外创建一个“同服务器共置的另一租户库”。
-- 目的：证明拒绝来自 DB 授权本身，而不仅仅是容器/网络边界——
-- 即便有人把两个租户放进同一台 DB 服务器，tenant_a 账号也读不到 tenant_b 库。
CREATE DATABASE IF NOT EXISTS tenant_b CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE TABLE IF NOT EXISTS tenant_b.canary (
    id INT PRIMARY KEY,
    secret VARCHAR(64) NOT NULL
);
INSERT INTO tenant_b.canary (id, secret) VALUES (1, 'TENANT-B-CANARY-COLOCATED');
