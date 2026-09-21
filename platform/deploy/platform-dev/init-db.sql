-- 运行期账号：不拥有任何表、不能绕过行级安全。表的所有权属于 platform_owner（迁移账号）。
CREATE ROLE platform_app LOGIN PASSWORD 'platform_app' NOSUPERUSER NOBYPASSRLS;
GRANT CONNECT ON DATABASE platform TO platform_app;
