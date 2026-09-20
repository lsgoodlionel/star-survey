-- 与 db-a.sql 对称：租户 B 的服务器上共置一个 tenant_a 库，用于反向验证。
CREATE DATABASE IF NOT EXISTS tenant_a CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE TABLE IF NOT EXISTS tenant_a.canary (
    id INT PRIMARY KEY,
    secret VARCHAR(64) NOT NULL
);
INSERT INTO tenant_a.canary (id, secret) VALUES (1, 'TENANT-A-CANARY-COLOCATED');
