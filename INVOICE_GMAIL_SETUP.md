# 見積・請求書・Gmail連携（実装中／公開未反映）

## 現在の検証状態（2026年11月13日）
- 本番Supabase台帳＋Mac上の実装＋実Gmailで、所有者が指定した宛先へのテストメール1通について `sent`／Gmail `SENT` を確認済み。公開Streamlit画面からの一連の操作を検証した結果ではありません。
- その実送信ではGmailが独自 `Message-ID` を書き換え、従来の `rfc822msgid` 検索で0件になることを確認しました。
- 対策として専用 `X-CYCA-Operation-ID` と台帳と同じ方式の `X-CYCA-Payload-SHA256` を送信MIMEへ追加。新しい照合方式は**模擬試験のみ**です。実Gmail送信後の専用ヘッダー保持は未検証。追加実送信は行っていません。
- 承認日時をUTC整数epochで検証し、前後5分の余裕を持つ日時範囲のSENT候補を100件×最大5ページ取得。From/To/Subject、専用ヘッダー、fingerprint、SENTラベルを厳密照合します。検索クエリーへ宛先文字列を直接挿入せず、アドレスはmetadataで照合します。
- 全ページを読み終えた場合だけ候補を返します。0件・複数一致・上限到達・API失敗・構造不正・要求IDとの不一致は未確認を維持し、自動再送しません。500件を超える範囲は管理者の別途確認が必要です。
- 専用ヘッダーがない旧メールは、元の独自Message-IDと送信元・宛先・件名などが厳密一致した場合のみ候補になります。書き換え済みの旧メールは件名だけで送信済みにしません。
- metadataは本文・添付そのものの検証ではありません。fingerprintヘッダーは台帳内容との整合性証拠であり、添付バイトを再取得した検査とは区別します。未知の送信結果を「未送信」と推定する用途には使いません。

以下は段階ごとの実装・検証記録です。「未実施」「未設定」の記述は、その節の作成時点の状態を表します。現在の到達点は上記を優先してください。

## 初期実装時点の記録

## 今回追加したもの
- `invoice_documents.py`: 承認済み見積からの請求書生成。金額の再読取なし。工事名1式・見積参照日・発行日・支払期限。確認用と正式PDFを区別。
- `invoice_registry.py`: 永続SQLiteに請求書番号と承認済みPDFを保存。同一番号の異なる内容を拒否。
- `invoice_workflow_ui.py`: 保存済み完成見積の画面から請求書確認PDFを作成するオプトイン画面。既存出力は変更しない。
- `document_delivery.py`: 宛先・本文・PDFの確認指紋、承認、送信、送信結果未確認、照合の管理。二重クリックをトランザクションで防止。実Gmail transportは認証済みGmail v1 serviceを注入する方式。
- `document_workflow_service.py`: UIと将来の認証済みボット接続で共用する内部呼出API。HTTP APIアダプタは追加済み。公開・dotコネクタ登録は未実施。

## 現段階の制約（運用開始不可）
会社Gmailのローカル認可・読取接続確認は完了。会社共通の本番永続保存先、社員権限、認証済みdot用アダプタ、実機送信E2Eは未完了。正式保存・送信UIは配線済みですが、現環境では設定未投入のため有効化していません。この追加だけで自然文から本番送信できるとは扱わない。
今回の旭土地建物向け実PDFを参照し、A4横の左右分割構成に適合。社印等の完全再現ではなく、所有者による最終見た目の確認が必要。

## 次の接続作業
1. 公開版の実ブランチと設定を確認する。既存のSupabase有効状態は未検証。
2. 会社情報・振込先・登録番号を既存の正式請求書から確認して設定。秘密情報はGitやログに入れない。
3. 非公開の永続DB/ストレージを決定。SQLite実装は単一サーバー・永続ボリューム用。Streamlit Cloudの一時ディスクでは本番送信を有効にしない。複数インスタンス運用ではトランザクション可能な共有DBへ置換する。
4. Gmail APIの会社送信元をOAuth認可。最小権限で送信と送信済み照合を可能にする。refresh tokenはサーバーの秘密管理にのみ保存。現在のdot Gmail接続をこのアプリが流用できるわけではない。
5. ログイン本人→会社・役割の照合を実装し、承認者と送信者を記録。社員ごとのIDを使い、同一パスワード共有はしない。
6. 認証済みボット用API/コネクタを用意。外部入力のowner/actorを信頼しない。レビュー承認なしにsendを呼ばせない。設定やコネクタ導入可否の確認が必要。
7. 本人のテスト用宛先で許可を得て、本番相当の作成→承認→送信→送信済み照合を検証。顧客には勝手にテスト送信しない。

## 確認用UIの設定（本番自動有効化なし）
Streamlit Secretsに `[invoice_workflow] enabled = true`、`[invoice_workflow.issuer] name = "..."`、`details = [...]`、`payment_details = [...]` を所有者の確定情報で設定。未設定なら従来画面のまま。

## メールのルール
顧客向けにはPDFのみ。本文に見積額を記載しない。ファイル名は工事名＋帳票名＋日付。内部の修正版・清書・調整などを付けない。宛先と添付を必ず照合する。返信時は既存thread IDとMessage-IDを引き継ぐ。Gmail送信結果にIDがなければ完了と言わない。
タイムアウトは送信失敗と同義ではない。sending/unknownは自動再送しない。Message-ID検索の0件は未送信の証明ではない。現状では不明状態を手動でapprovedへ戻す機能も置いていない。

## テスト
`python3 -m unittest test_invoice_documents test_document_delivery test_invoice_registry test_invoice_workflow_ui test_workflow_api test_saved_quotes`
実Gmail送信・Google認可・公開版動作の検証とは区別する。

## 追加配線（現在の実装）
- 永続保存先設定済みの場合、確認用UIから請求書確定・番号重複検査・正式PDFダウンロード・送信前確認・Gmail送信・結果照合まで配線済み。
- 既存完成見積PDFにも同じ送信前確認パネルを追加。
- `workflow_runtime.configured_service` は認可済みOAuth JSONファイルのみ読み込む。認可画面やトークン作成は行わない。`database_path` は所有者のみアクセス可能な専用ディレクトリとし、管理者が `persistent_storage_verified=true` を設定する。
- `google_oauth_credentials_file`、`gmail_enabled=true`、`sender` が揃い、所有者が確認ボタンを押したときのみ送信。未設定はブロック。
- `workflow_api.create_app` はボット用HTTPアダプタ。token_ownersには既存の認可済みトークンのSHA256と利用者IDの対応をサーバーから渡す。コードに秘密を埋め込まない。resolve_estimate/resolve_documentは認証された利用者の保管済み・確定済みデータだけを返す必要がある。HTTPサーバーの起動・公開・コネクタ登録はまだ行っていない。
- `/invoices/preview`, `/invoices/preview/pdf`, `/invoices/finalize`, `/deliveries/prepare`, `/deliveries/{id}`, `/deliveries/{id}/approve`, `/send`, `/reconcile` を提供。準備・承認・送信は分離。HTTPS終端と利用者別権限の設定を済ませてから使用する。

## 独立レビュー後の制限
- 会社IDと操作した社員IDを分離済み。company_id/company_members、APIのactor_companiesをサーバーで設定し、会社単位で採番/重複防止、社員単位で承認を記録。設定なしは単独利用互換。実際の社員権限・会社共有vault対応を確認するまで社員共通運用は開始しない。
- 同じ宛先かつ同一文書IDまたは同一PDFハッシュの未取消レコードは、新しいrequest_keyでも再利用/拒否する。本文・ファイル名を変えて再送ガードを回避させない。sent後の意図的再送機能は現時点で提供しない。
- 未送信review/approvedのみ明示取消可能。sending/unknown/sentは取消による再送不可。
- workflow_vault_adapter.pyは既存承認済み見積PDFと確定請求書DBを結ぶ実アダプタ。configured_apiは会社設定・認可情報を検証してAPIを構築する。vault_factory(company_id)を本番の許可済み会社保管庫へ結ぶ設定は未投入。複数候補PDFは勝手に選ばず停止する。
- 独立レビュー後、対象31テスト成功。既存全体テストの4失敗3エラーは元ブランチでも再現した。全体が緑ではないことを維持して報告する。

## Supabase Postgres版（コード追加／実DB未変更）
`database_provider = "postgres"` で `PostgresDeliveryStore` を利用。`postgres_dsn` またはサーバー環境変数 `CYCA_WORKFLOW_POSTGRES_DSN` は、別途許可・設定された専用接続情報を使用する。Supabase Storageのservice_keyを流用しない。TLS verify-fullを必須にする。秘密値はブラウザー・チャット・Git・ログへ出さない。

`workflow_schema.sql` は既存バケットを変更せず新しい非公開schemaを作る移行案。起動時に自動実行しない。実行前に既存Supabaseプロジェクト/バックアップ/対象DBと影響を確認し、移行の承認を得る。runtime専用roleと最小権限、RLS policy、DSNの管理はまだ未設定。公開PostgREST schemaやanon/authenticatedには開放しない。

短いDB transactionごとにadvisory transaction lockを取得し、複数サーバー・社員でのprepare/finalizeを直列化。DB一意indexでも会社＋宛先＋文書ID/PDFハッシュの重複を防ぐ。メールAPI呼出中はDBロックを持たない。

当初はmock driver/SQL構造のみを検証。その後の実Postgres隔離試験は下記参照。Supabaseへの実接続・移行・権限・障害復旧・並行送信テストは未実施。これらを完了するまで本番送信を有効にしない。

確認済み公開元は `cycakensetsu/cyca-smart-link` の `main` / `extract_data.py`、Python3.13。2026-11-12確認時mainは108bf31。既存SecretsにはSupabase共通保存設定あり、新しいinvoice_workflow設定はなし。秘密の値は記録していない。

## 社員権限と最終検証
共有運用は `company_id` / `company_members` に加え `member_permissions` をサーバーで設定。権限は `read`, `prepare`, `approve`, `send`。操作前に権限検査し、readなしで実行後だけエラーになる動作を防ぐ。取消にはapprove+readが必要。実際の所属・権限値は未設定。
2026-11-12 最終関連47テスト成功（SQL/driverテスト8件を含む）。SQLite/偽Gmail/HTTP TestClient/Streamlit AppTest、架空請求PDFの視覚確認。この時点では実環境未検証。その後の実Postgres/実Gmail認可確認は下記参照。全体試験は521件中4失敗3エラー7skipで、同じ既存失敗は元ブランチで再現。追加の最終差分は関連テストで再検証。

## 2026-11-12 実PostgreSQL隔離試験
所有者の承諾を得てHomebrew公式PostgreSQL17.11をMacへ導入。自動起動サービスは登録せず、既存本番とは別の/tmp専用clusterを使い、TCP待受なし・所有者専用Unix socketのみで実施。
`test_postgres_live.py` の9件すべて成功：8並列prepareが1記録、4並列請求書確定が1PDF、実DBのdoc/hash一意制約、会社スコープ、unknown時の新UUID/取消/再送拒否、非管理者runtimeとreadonly UPDATE拒否、migration/RLS、実MIME生成＋偽Gmail4並列で1送信。
試験後pg_ctlで停止し、no server running・専用socket消失・brew services postgresql@17=noneを確認。Homebrewが作成した標準の空clusterは起動していない。
本番Supabaseへの接続/移行、本番Gmail送信は未実施。実試験はローカルUnix socket限定であり、本番TLS接続・本番権限・Google認可の検証を代替しない。
47件の既存関連回帰テストも再実行し、全件成功（skipなし）。

## Gmail設定の初期調査（認可前の記録）
会社アカウントのGoogle Cloud認証情報画面へアクセス可能。確認時に選択可能な既存プロジェクトは見当たらず、接続用project/OAuth clientの準備が必要。無料トライアルや課金は申し込んでいない。
現在のGmail transportは送信にgmail.send、送信結果の照合検索にgmail.readonlyを要する。readonlyはメール閲覧権限を含むため、接続先アプリと会社アカウント、必要範囲を所有者に明示して認可を得てから設定する。OAuth秘密/refresh tokenはチャット・Git・顧客添付へ出さない。

## 2026-11-12 Gmail本人認可・読取確認（送信なし）
所有者の個別承諾を受け、会社Googleアカウントに専用Cloud project「CYCA Smart Link Gmail」を作成。請求先や有料トライアルは追加していない。Google APIユーザーデータポリシーへの同意、OAuthテスト構成、ローカル認可用Desktop client、Gmail API有効化、会社Gmailのみのテストユーザー登録を実施。
未確認アプリの警告についても所有者了承後に続行。gmail.send と gmail.readonly のみ認可し、公式Gmail API getProfileで会社アカウントと一致することを検証。refresh tokenはGit外の専用保管先に本人限定権限で保存。秘密値や認可コードをドキュメントへ記載しない。
実メール送信は0件。Streamlit公開版への認可情報投入、DB本番移行、公開API、dot連携の登録は未実施。

### 継続運用と複数Gmailの注意
- 現在External/Testing。Gmail scopeを含むrefresh tokenは7日で失効。自動再認可でこの制限を回避せず、期限切れは再接続が必要と明示する。
- 各送信元は本人のOAuth認可とアカウント一致検証が必要。他社員へ会社トークンを暗黙共有しない。会社共通の採番/重複防止と、利用者別の送信元/権限を分離する。
- 個人GmailはGoogleのInternalアプリ対象ではない。Internalは組織所有projectと同一Workspace/Cloud Identity組織内ユーザーが条件。
- 本人/個人的に知る少人数の利用には審査例外があり得るが、全社・第三者への一般提供へ自動的に拡張しない。Production変更だけで審査済みにはならない。
- gmail.sendはSensitive、gmail.readonlyはRestricted。一般公開でサーバーがRestrictedデータを扱う場合、例外を除きRestricted scope審査と年次セキュリティ評価が必要。対象ユーザー・実際のデータフローを確定して判断する。
- 送信だけへscopeを減らす場合は、unknown照合を失わない別設計が必要。無断再送を許可する代替にはしない。
公式資料（2026-11-12確認）:
https://support.google.com/cloud/answer/15549945?hl=en
https://developers.google.com/workspace/gmail/api/auth/scopes
https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification
https://developers.google.com/identity/protocols/oauth2/production-readiness/overview

### 利用者別Gmail設定（本番への設定投入は別途）
複数利用者はサーバー側 `gmail_accounts` にログインactorごとの `sender`, `google_oauth_credentials_file`, `gmail_enabled` を持たせる。リクエスト本文のsender/actorやファイルパスでこの対応を変更しない。各人の認可は本人のGoogle画面で行い、保存後のアカウント一致を検証する。
共有会社では同じ文書/宛先の重複防止は会社全体で維持し、別の送信元へ変えて送信履歴のガードを逃れない。送信/照合はその利用者に対応したメールボックスだけで実行。会社内の閲覧・帳票作成権限とメール連携の有無は別として管理する。
本番配置ではローカル認可JSONをリポジトリ/静的配信/共有PDFストレージへコピーしない。専用の秘密管理と実行ユーザーだけが読める一時ファイル等を設計・確認し、保存先が確定して許可されるまで転送しない。

2026-11-13 利用者別Gmail分離を実装・独立レビュー済み。Streamlit Secrets実形式（AttrDict）も回帰試験へ追加。関連55テストすべて成功、diff --check正常。送信元偽装/他社員のsend・reconcile拒否、会社重複防止、Gmail未連携社員の帳票・閲覧維持を検証。実メール送信E2Eと本番配置は別途未完了。

## 2026-11-13 本人宛て実送信試験
所有者が指定した会社Gmail→同じ会社Gmailへテストメール1通を送信。架空の確認用請求PDFを添付し、請求/発注でない旨を明記。送信元/宛先一致、GmailのSENTとINBOXをAPIで照合し、永続ローカル台帳のsent状態も確認。固定の業務キーで二重送信を防止。顧客宛て送信なし。
これはローカル台帳＋実GmailのE2E確認で、本番Supabase/Streamlit公開版/ボット経由の検証ではない。

### Streamlit Secretsでの利用者別認可情報（サーバー設定のみ）

Mac上の認可JSONファイルを参照できない公開サーバーでは、各 `invoice_workflow.gmail_accounts.<actor>` の `authorized_user` に、本人が認可したGoogle OAuth authorized-user情報を非公開設定として格納できます。Streamlit Secretsの `AttrDict` を含むMappingに対応しています。これは認可フローの代替ではなく、取得済み認可情報の読込方法です。本変更は公開サーバーへの転送や設定を実行しません。

- 必須：`client_id`、`client_secret`、`refresh_token`、`scopes`。空の値は拒否します。
- `type` は省略可能（Google `Credentials.to_json()` の互換性）。指定する場合は `authorized_user` のみ。
- `scopes` は文字列配列または空白区切り文字列で、既存の認可に `https://www.googleapis.com/auth/gmail.send` と `https://www.googleapis.com/auth/gmail.readonly` の両方が必要です。文字列を書き足して権限を取得したことにしてはいけません。不足時は本人のGoogle再認可が必要です。
- `token_uri` を指定する場合は `https://oauth2.googleapis.com/token` のみ。`universe_domain` を指定する場合は `googleapis.com` のみ。
- `google_oauth_credentials_file` と `authorized_user` は**同時指定禁止**。既存の所有者限定JSONファイル方式も維持し、同じ必須項目・scope検証を行います。
- `sender` と `gmail_enabled` は引き続きactor別。複数社員構成で他actorやグローバル認可情報へのフォールバックはしません。
- 認可情報はブラウザー、API要求本文、Git、ログ、ダウンロード帳票には含めません。SDK・JSONの例外も固定メッセージへ置換します。
- 実送信・送信済み照合時にはGoogleアカウントの実メールアドレスとactorの `sender` を再照合します。
- トークンが更新されてもSecretsへの自動書戻しは行いません。失効・再認可時は管理者が非公開設定を更新します。Google Testingの期限など既存制約も変わりません。

`authorized_user` の追加・移動は認証情報の取扱いです。対象プロジェクト・actor・保存先の許可を確認してから行い、値をチャットへ貼り付ける運用にしないでください。
