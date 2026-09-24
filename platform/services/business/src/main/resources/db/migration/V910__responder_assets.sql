-- 作答者上传的资产（R02-25/26）与按作答者身份授权（R02-39/41 前置）。
-- ADR 0019 决定 8 与决定 9，模块号段 V910–V919。
--
-- 作答者上传与作者素材放在同一张 platform_asset 里——它们是同一种二进制 ＋ 同一套版本、
-- 存储键与取件机制，另起一张表会把上传闸门、租户隔离与删除守卫各抄一份，必然漂移。
-- 区别由 origin 一列承担，而那一列是一组规则的开关，不是标签：
--   respondent 的资产不进素材库列表、不许追加版本、不许从库里删、**不许被问卷定义引用**
--   （后者是安全要求：一旦被引用，发布会把它改写成一张 bearer 取件票发给所有作答者）。

ALTER TABLE platform_asset
    ADD COLUMN origin text NOT NULL DEFAULT 'author' CHECK (origin IN ('author', 'respondent'));

-- 作答者身份只存**不可逆指纹**，绝不存参与者令牌本身：
-- 平台库里不该多出一份可以直接进入问卷的凭据（与 GatewayRespondentIdentityResolver 同一条自律）。
ALTER TABLE platform_asset
    ADD COLUMN respondent_key text CHECK (respondent_key ~ '^[0-9a-f]{64}$');

-- 作者素材不该有身份绑定。反过来不成立：匿名作答的上传没有令牌，指纹为空是正常的，
-- 它的后果是「一张绑定票都签不出来」（失败即关闭），不是「入不了库」。
ALTER TABLE platform_asset
    ADD CONSTRAINT platform_asset_author_has_no_respondent
    CHECK (origin = 'respondent' OR respondent_key IS NULL);

-- 默认值只为让既有行落到 author；新写入一律显式给值，免得将来加第三种来源时悄悄落到默认。
ALTER TABLE platform_asset ALTER COLUMN origin DROP DEFAULT;

-- 作答者上传的来源证据：这件资产是哪台引擎、哪份答卷、哪道题上传的。
-- 作者审阅答卷时靠它找到那段录音；同时它也是「资产行由上传会话驱动」这句话的存档。
CREATE TABLE platform_asset_intake (
    tenant_id          uuid        NOT NULL,
    -- 即上传会话的 upload_token（ADR 0006 决定 4 预留的那一列）。
    asset_id           uuid        NOT NULL,
    engine_instance_id text        NOT NULL CHECK (length(engine_instance_id) BETWEEN 1 AND 64),
    engine_sid         bigint      NOT NULL CHECK (engine_sid > 0),
    generation         text        NOT NULL CHECK (length(generation) BETWEEN 1 AND 36),
    -- 上传发生时答卷可能还没有 id（引擎分两步且中间没有事件），会话表里记 0，这里照搬。
    response_id        bigint      NOT NULL CHECK (response_id >= 0),
    question_code      text        NOT NULL CHECK (length(question_code) BETWEEN 1 AND 64),
    ingested_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, asset_id),
    FOREIGN KEY (tenant_id, asset_id) REFERENCES platform_asset (tenant_id, id) ON DELETE CASCADE
);

-- 「这份答卷有哪些上传」是唯一的读法。
CREATE INDEX platform_asset_intake_by_response
    ON platform_asset_intake (tenant_id, engine_instance_id, engine_sid, generation, response_id);

-- 来源证据不可改：改一行等于「这段录音换了个主人」。
-- DELETE 保留：资产随答卷的保留策略删除时要跟着走（级联）。
REVOKE UPDATE ON platform_asset_intake FROM platform_app;

ALTER TABLE platform_asset_intake ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform_asset_intake FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON platform_asset_intake
    USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());
