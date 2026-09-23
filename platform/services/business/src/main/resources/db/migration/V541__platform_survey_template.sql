-- 平台（运营）发布的模板库（R19-05，WP-19 切片 19.2）。
--
-- 这两张是**控制平面表**，故意不做行级安全：一份运营模板要对所有租户可见，
-- 而行级安全的前提恰恰是「只看得见自己租户的行」。可见性改由查询条件承担——
-- 租户侧的读取只查 status = 'published'，写入只走 /v1/platform/** 下的运营端点
-- （PlatformWebConfig 的拦截器要求 platform_operator 角色）。
--
-- 运营模板里没有任何租户数据：它的定义同样经过剥离，且来源是运营自己提交的定义，
-- 不是某个租户的问卷。
--
-- status：draft ──> published ──> unpublished ──> published（改版后重新上架）
-- 下架只影响之后的复制；已复制出去的问卷是租户自己的独立草稿。
CREATE TABLE platform_survey_template (
    id                uuid        PRIMARY KEY,
    name              text        NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    description       text        CHECK (description IS NULL OR length(description) <= 1000),
    status            text        NOT NULL CHECK (status IN ('draft', 'published', 'unpublished')),
    current_version   integer     NOT NULL CHECK (current_version > 0),
    published_version integer     CHECK (published_version IS NULL OR published_version > 0),
    created_by        text        NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CHECK ((status = 'published') = (published_version IS NOT NULL)),
    CHECK (published_version IS NULL OR published_version <= current_version)
);

CREATE INDEX platform_survey_template_published
    ON platform_survey_template (updated_at) WHERE status = 'published';

CREATE TABLE platform_survey_template_version (
    template_id uuid        NOT NULL REFERENCES platform_survey_template (id),
    version_no  integer     NOT NULL CHECK (version_no > 0),
    definition  jsonb       NOT NULL,
    created_by  text        NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (template_id, version_no)
);

-- 最小授权：运营模板不删除（下架即可）；版本快照只追加。
REVOKE DELETE ON platform_survey_template FROM platform_app;
REVOKE UPDATE, DELETE ON platform_survey_template_version FROM platform_app;
