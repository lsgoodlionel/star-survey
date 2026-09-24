-- 平台资产服务（ADR 0019，模块号段 V901–V909；V900 归字典车道）。七类插件题型（R02-20/21/25/26/32/39/41）的前置能力。
--
-- 三张表分工：
--   platform_asset            一件资产（名称、类别、状态、当前版本号），名称与状态可改；
--   platform_asset_version    一个版本的字节在哪、多大、什么类型、摘要是多少，**内容不可改**；
--   platform_asset_reference  哪一版问卷引用了哪一件资产的哪一版，**只追加**。
--
-- 字节永远不进 PostgreSQL（与 ADR 0015 的导出文件同一口径）：库里只有 storage_key + byte_size + sha256，
-- 字节在 BlobStore 里。存储键一律由平台生成，从不含调用方文本。

CREATE TABLE platform_asset (
    tenant_id       uuid        NOT NULL,
    id              uuid        NOT NULL,
    -- 类别由内容嗅探结果推出，不由调用方声明；只有这三类。
    kind            text        NOT NULL CHECK (kind IN ('image', 'audio', 'video')),
    name            text        NOT NULL CHECK (length(name) BETWEEN 1 AND 256),
    -- archived＝停用：不再能被新的发布引用，已发布的问卷照常回放。
    status          text        NOT NULL CHECK (status IN ('active', 'archived')),
    current_version integer     NOT NULL CHECK (current_version >= 1),
    created_by      text        NOT NULL CHECK (length(created_by) BETWEEN 1 AND 256),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id)
);

-- 资产库按"最近上传的在前"翻页。
CREATE INDEX platform_asset_recent ON platform_asset (tenant_id, created_at DESC, id);

CREATE TABLE platform_asset_version (
    tenant_id     uuid        NOT NULL,
    asset_id      uuid        NOT NULL,
    version_no    integer     NOT NULL CHECK (version_no >= 1),
    -- BlobStore 的键：asset/<租户>/<资产>/v<版本>，字符集与 BlobStore.KEY 一致。
    storage_key   text        NOT NULL CHECK (storage_key ~ '^[a-z0-9][a-z0-9./-]{0,255}$'),
    -- 嗅探出来的内容类型，不是调用方声明的那个。
    content_type  text        NOT NULL CHECK (length(content_type) BETWEEN 1 AND 128),
    byte_size     bigint      NOT NULL CHECK (byte_size > 0),
    sha256        text        NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    -- 只作为元数据留档，绝不参与存储键。
    original_name text        NOT NULL CHECK (length(original_name) BETWEEN 1 AND 256),
    created_by    text        NOT NULL CHECK (length(created_by) BETWEEN 1 AND 256),
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, asset_id, version_no),
    -- 复合外键带 tenant_id：外键检查绕过行级安全，不带就能跨租户挂版本（第二波组织免登审查的结论）。
    FOREIGN KEY (tenant_id, asset_id) REFERENCES platform_asset (tenant_id, id) ON DELETE CASCADE
);

CREATE TABLE platform_asset_reference (
    tenant_id      uuid        NOT NULL,
    asset_id       uuid        NOT NULL,
    version_no     integer     NOT NULL,
    survey_id      uuid        NOT NULL,
    -- 引用是在哪一次发布尝试里登记的，便于回溯"这份快照用的是第几版"。
    draft_version  integer     NOT NULL CHECK (draft_version >= 1),
    created_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, asset_id, version_no, survey_id, draft_version),
    FOREIGN KEY (tenant_id, asset_id, version_no)
        REFERENCES platform_asset_version (tenant_id, asset_id, version_no),
    FOREIGN KEY (tenant_id, survey_id) REFERENCES survey (tenant_id, id)
);

-- "这件资产还有人引用吗"是删除守卫的唯一判据（不做引用计数整数，计数会漂移）。
CREATE INDEX platform_asset_reference_by_asset ON platform_asset_reference (tenant_id, asset_id);

-- 版本的内容不可改：改一行等于"已发布的问卷里那张图被换掉了"，而版本留存正是本服务存在的理由。
-- DELETE 保留：无人引用的整件资产可以连同字节一起删掉（级联到版本行）。
REVOKE UPDATE ON platform_asset_version FROM platform_app;
-- 引用行是"哪一版问卷用了哪一版资产"的证据，既不能改也不能删。
REVOKE UPDATE, DELETE ON platform_asset_reference FROM platform_app;

ALTER TABLE platform_asset ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform_asset FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON platform_asset
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE platform_asset_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform_asset_version FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON platform_asset_version
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

ALTER TABLE platform_asset_reference ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform_asset_reference FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON platform_asset_reference
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
