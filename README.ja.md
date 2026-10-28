<!-- README.md の 96086f6f42cfaa31530cb658e47931eef0ca47f8 を基にした翻訳草稿です。技術面と言語面のレビューが必要です。 -->
[简体中文](README.md) · [English](README.en.md) · [日本語](README.ja.md)

# Survey · LimeSurvey を基盤とする中国語向けアンケートプラットフォーム

Survey（リポジトリ名: star-survey）は、LimeSurvey 7.1.2 エンジンの周辺に構築された、中国語のアンケート管理・配信プラットフォームです。マルチテナント SaaS とプライベート環境への導入を対象としています。独自コードの主な配置先は `platform/` と `plugins/` です。エンジンのソースは上流のバージョンを維持し、プラットフォームは管理されたインターフェース、公開ゲートウェイ、プラグインを介して連携します。

> 2026-10-28 時点の状況: Wave 2 のテンプレートとブランド機能は、MySQL と PostgreSQL の両構成を用いたローカル Docker 環境で機能検証を完了しました。Wave 3 のデータ機能は開発中です。正式な GitHub Release、公開 TLS、本番環境へのインストール、アップグレード、復旧はまだ検証を完了していません。このリポジトリを、本番利用の準備が整った安定版と見なさないでください。

## 検証済みの製品フロー

| 対象 | 現在の進捗 |
| --- | --- |
| 管理ワークスペース | 中国語の Dashboard、プロジェクトとアンケートの作業コンテキスト、最近の作業、ロール別の表示範囲を実ブラウザーで検証済みです。 |
| テンプレートとブランド（Wave 2） | テナント・プラットフォームのテンプレート閲覧、承認、コピー、テーマカラー、タイトル、フッター、Logo、favicon、多言語設定、画像付きプレビュー、実際の回答、公開版のアセット固定について、ローカル機能検証を完了しました。 |
| 公開と回答 | 分離されたプレビュー、承認を伴う公開、一般公開の回答、回答の読み戻しが一連のフローとしてつながっています。完全なデータワークスペースとエクスポートタスクの体験は Wave 3 の対象です。 |
| 本番環境への導入 | 単一ホスト向け本番 Compose、`surveyctl`、Release パイプラインには実装と自動チェックがあります。実際の Release と本番運用のライフサイクルは、別途検証が必要です。 |

Wave 2 の検証は、MySQL と PostgreSQL をそれぞれ使う分離された Docker スタックで実施しました。管理画面の実ブラウザーテストは両構成とも **19/19** 件を通過し、プラットフォーム API、プラットフォーム DB、公開ゲートウェイ、エンジン DB も確認しました。この結果が示すのは、計画されたローカル機能範囲に限られます。より広い要件を扱う[機械可読の機能一覧](platform/docs/productization/capability-map.md)には、未完了の項目が残っています。

[現在の進捗](docs/agent/PROGRESS.md) · [製品の設計方針](platform/docs/productization/product-blueprint.md) · [Wave 0–6 のロードマップ](platform/docs/productization/release-roadmap.md) · [プラットフォーム技術文書](platform/README.md)

## システム構成

~~~mermaid
flowchart LR
    A[作者浏览器] --> W[管理端 React / TypeScript]
    W --> P[平台 Spring Boot / Java 21]
    P --> D[(PostgreSQL：租户与业务数据)]
    P --> G[发布网关 Python]
    G --> E[LimeSurvey 引擎与自有插件]
    E --> M[(MariaDB / MySQL 或 PostgreSQL)]
    R[作答者浏览器] --> E
    E -->|签名事件| P
~~~

プラットフォームはテナント、権限、アンケート定義、公開版、回答の投影データを管理します。ゲートウェイはエンジンとの連携を制御します。独自プラグインはエンジン側のイベント、回答ポリシー、ブランドアセットの経路を扱います。契約と信頼境界は[プラットフォーム技術文書](platform/README.md)を参照してください。

## ローカルでフロントエンドを確認する

Docker Engine、Docker Compose、ローカルで使える Python、プロジェクトに必要なビルドツールが必要です。リポジトリには、本番設定を使用しない独立した永続デモスタックがあります。

~~~bash
bash platform/deploy/demo/run-admin-web-demo.sh start
bash platform/deploy/demo/run-admin-web-demo.sh status
~~~

起動後、アクセス先、テスト用 ID、認証情報はローカルの `platform/deploy/demo/.runtime/access.json` に書き込まれます。ディレクトリとファイルの権限はそれぞれ 0700、0600 に制限され、Git にはコミットされません。デモの管理画面はテストトークンでログインします。`access.json` にある `adminWebUrl` の末尾に `/dev/token` を付けて開き、`platformAccessToken` を入力してください。エンジン管理画面のアカウントは同じファイルに別途記載されています。デモサービスは `127.0.0.1` のみにバインドされます。ポートは Docker が動的に割り当てるため、ファイル内の実際の URL を使用してください。

~~~bash
# 令牌过期后刷新；停止服务时保留演示数据
bash platform/deploy/demo/run-admin-web-demo.sh refresh-token
bash platform/deploy/demo/run-admin-web-demo.sh stop
~~~

テストトークンの入口はローカルデモとエンドツーエンドテスト専用です。本番環境の入口と導入要件は[本番運用ガイド](platform/deploy/production/README.md)を参照してください。

## 開発と品質チェック

管理画面は Node.js 22、プラットフォームは Java 21 を使用します。次のコマンドはリポジトリのルートから実行します。

~~~bash
# 管理端静态检查、测试与构建
cd platform/apps/admin-web
npm ci
npm run lint
npm run typecheck
npm test -- --run
npm run build
cd ../../..

# 发布网关单元测试
cd platform/tools/publish-gateway
python3 -m unittest discover -s tests -t . -q
cd ../../..

# 平台 Java 全套测试
platform/deploy/test/run-platform-tests.sh
~~~

DB を使う実際のフローは、MySQL と PostgreSQL で個別に検証します。管理画面のエンドツーエンドフローの例を示します。

~~~bash
TEST_DB=mysql SURVEY_TEST_PREFIX=readme-mysql COMPOSE_PROJECT_NAME=readme-mysql \
  bash platform/deploy/test/run-admin-web-e2e.sh --fresh
TEST_DB=pgsql SURVEY_TEST_PREFIX=readme-pgsql COMPOSE_PROJECT_NAME=readme-pgsql \
  bash platform/deploy/test/run-admin-web-e2e.sh --fresh
~~~

[GitHub の製品品質ワークフロー](.github/workflows/platform-quality.yml)は、フロントエンド、Java プラットフォーム、公開ゲートウェイ、実ブラウザー、アクセス制御、両 DB 構成を検査します。Wave 2 完了時の[製品品質実行](https://github.com/lsgoodlionel/star-survey/actions/runs/40612142008)は **10/10** 件成功しました。[Release ビルド実行](https://github.com/lsgoodlionel/star-survey/actions/runs/40612142061)では、admin、platform、publish-gateway の AMD64/ARM64 読み取り専用イメージビルドが完了しました。

[Release ワークフロー](.github/workflows/release.yml)は候補版タグで起動した後にのみ、イメージ公開、署名、成果物生成、実ホストでの検証を行います。PR のビルド成功は正式な Release の作成を意味しません。現在の本番検証範囲と操作コマンドは[本番運用ガイド](platform/deploy/production/README.md)を参照してください。

## 文書とプロジェクトの境界

- [フロントエンド・バックエンドの要件と検証項目](platform/docs/productization/frontend-backend-requirements.md)
- [製品の設計方針](platform/docs/productization/product-blueprint.md)
- [公開ロードマップ](platform/docs/productization/release-roadmap.md)
- [機能の状態と証拠](platform/docs/productization/capability-map.md)
- [Wave 2 完了状況と次の作業](docs/agent/PROGRESS.md)

このリポジトリは [LimeSurvey](https://www.limesurvey.org/) を基盤としています。元のプロジェクト、商標、素材の権利はそれぞれの権利者に帰属します。コードのライセンスは [LICENSE](LICENSE)（GPL-2.0-or-later）を参照してください。本プロジェクトの状態と機能については、リポジトリのコード、機能一覧、対応する検証結果を根拠としてください。
