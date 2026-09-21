-- 容器的引导超级用户是 postgres，只用于初始化；平台自己的两个账号都不是超级用户。
--   platform_owner：执行迁移、拥有表与数据库；非超级用户，因此 FORCE ROW LEVEL SECURITY 对它同样生效。
--   platform_app：应用运行账号；不拥有任何表、不能绕过行级安全。
-- 若 platform_owner 是超级用户，它会无视行级安全，那些"只对属主开放"的策略就等于没测到。
CREATE ROLE platform_owner LOGIN PASSWORD 'platform_owner' NOSUPERUSER CREATEDB NOBYPASSRLS;
CREATE ROLE platform_app LOGIN PASSWORD 'platform_app' NOSUPERUSER NOBYPASSRLS;
CREATE DATABASE platform OWNER platform_owner;
GRANT CONNECT ON DATABASE platform TO platform_app;
