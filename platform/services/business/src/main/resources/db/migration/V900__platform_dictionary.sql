-- 平台层级字典（WP-02 R02-03 多级下拉的前置能力，ADR 0019）。
--
-- 三张**控制平面表**，与 platform_survey_template 同一口径：故意不做行级安全，
-- 因为一本行政区划字典要对所有租户可见，而行级安全的前提恰恰是「只看得见自己租户的行」。
-- 写入只走 /v1/platform/** 下的运营端点（PlatformWebConfig 的拦截器要求 platform_operator），
-- 租户侧的读取只查已发布的版本。表里没有任何租户数据：字典是参考数据，不是谁的答卷。
--
-- 为什么必须版本化：字典会更新（区县撤并、改名），而已发布问卷引用的必须是确定的那一版，
-- 否则半年前那份答卷里的「XX 区」会在今天的字典里变成非法路径。版本一旦发布即不可变，
-- 发布时算出 digest，引擎侧的快照按 digest 核对（与 ADR 0012 的 structureVersion / policyDigest 同类）。

CREATE TABLE platform_dictionary (
    code            text        PRIMARY KEY CHECK (code ~ '^[a-z][a-z0-9-]{1,63}$'),
    name            text        NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    -- 树最深几层。行政区划到区县是 3；留到 8 足够容纳门店、品类这类更深的字典。
    max_depth       integer     NOT NULL CHECK (max_depth BETWEEN 1 AND 8),
    -- 新发布的问卷会固化到这一版；为空表示这本字典还没有任何一版发布过。
    current_version text,
    created_by      text        NOT NULL CHECK (length(created_by) BETWEEN 1 AND 256),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE platform_dictionary_version (
    dictionary_code text        NOT NULL REFERENCES platform_dictionary (code),
    -- 与副表契约的 structureVersion 同一字符集：这一版会被原样写进题目属性带到引擎。
    version         text        NOT NULL CHECK (version ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$'),
    status          text        NOT NULL CHECK (status IN ('draft', 'published')),
    -- 发布时按规范化节点表算出，之后不可改；引擎侧快照据此核对是不是同一份数据。
    digest          text        CHECK (digest IS NULL OR digest ~ '^dg1:[0-9a-f]{16}$'),
    node_count      integer     NOT NULL DEFAULT 0 CHECK (node_count >= 0),
    published_at    timestamptz,
    created_by      text        NOT NULL CHECK (length(created_by) BETWEEN 1 AND 256),
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dictionary_code, version),
    -- 草稿没有摘要、已发布必有摘要：状态与不可变性是同一件事的两面。
    CHECK ((status = 'published') = (digest IS NOT NULL)),
    CHECK ((status = 'published') = (published_at IS NOT NULL))
);

-- 当前版本必须真的存在且已发布。分两条约束写不出来（指向自身的复合外键），
-- 所以在这里加一条复合外键，由服务保证只把 published 的版本设成 current。
ALTER TABLE platform_dictionary
    ADD CONSTRAINT platform_dictionary_current_version_fk
    FOREIGN KEY (code, current_version) REFERENCES platform_dictionary_version (dictionary_code, version)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE platform_dictionary_node (
    dictionary_code text        NOT NULL,
    version         text        NOT NULL,
    -- 节点代码在**一版之内全局唯一**（行政区划的 GB/T 2260 代码本来就是这样）。
    -- 这样一条作答只存代码就够了，校验路径不必逐级拼接父代码。
    code            text        NOT NULL CHECK (code ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$'),
    parent_code     text        CHECK (parent_code IS NULL OR parent_code ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$'),
    depth           integer     NOT NULL CHECK (depth BETWEEN 1 AND 8),
    label           text        NOT NULL CHECK (length(label) BETWEEN 1 AND 200),
    -- 提交顺序，决定下拉框里的先后；同层内唯一由服务保证（按提交序号生成）。
    sort_key        integer     NOT NULL CHECK (sort_key >= 0),
    -- 有没有子节点。发布时按实际的父子关系算出来，不由导入方声明。
    is_leaf         boolean     NOT NULL,
    -- 关键字搜索用的归一化文本（label 去空白转小写）。
    search_text     text        NOT NULL,
    PRIMARY KEY (dictionary_code, version, code),
    FOREIGN KEY (dictionary_code, version)
        REFERENCES platform_dictionary_version (dictionary_code, version),
    -- 根节点深度必须是 1，非根必须更深：深度递增本身就排除了环。
    CHECK ((parent_code IS NULL) = (depth = 1))
);

-- 按父节点分页：作答页一次只要一层的一页。
CREATE INDEX platform_dictionary_node_children
    ON platform_dictionary_node (dictionary_code, version, parent_code, sort_key, code);
-- 关键字搜索：前缀命中走索引，包含命中退化成扫一版（节点数有上限，见 DictionaryLimits）。
CREATE INDEX platform_dictionary_node_search
    ON platform_dictionary_node (dictionary_code, version, search_text);

-- 已发布的版本与它的节点都不可改写：改一个字就等于换一版。
-- 草稿的节点要能改（重新导入），所以 node 只收回 UPDATE，删除由服务在草稿状态下做。
REVOKE UPDATE ON platform_dictionary_node FROM platform_app;
REVOKE DELETE ON platform_dictionary_version FROM platform_app;
