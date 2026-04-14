"""
Dashboard UI テキスト翻訳辞書。

使用方法:
    from translations import get_translations
    T = get_translations(st.session_state.get("lang", "日本語"))
    st.title(T["title"])
"""

from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    # ------------------------------------------------------------------ #
    # 日本語
    # ------------------------------------------------------------------ #
    "日本語": {
        # app.py
        "page_title": "MAS AgentCore Dashboard",
        "nav_chat": "Agent Chat",
        "nav_evaluation": "Evaluation Logs",
        "nav_visualization": "Visualization",
        "nav_threat_modeling": "Threat Modeling",

        # ── chat.py ──────────────────────────────────────────────────── #
        "chat_title": "💬 Broken MAS Chat",
        "chat_caption": "ローカルオーケストレーターとチャットします。`/invocations` を呼び出します。",
        # sidebar sections
        "chat_section_connection": "📡 接続設定",
        "chat_label_orchestrator_url": "オーケストレーター URL",
        "chat_help_orchestrator_url": "オーケストレーターのエンドポイント URL（末尾の /invocations は自動付与）",
        "chat_label_region": "AWS リージョン",
        "chat_help_region": "Guardrail を使用する場合に必要なリージョン",
        "chat_label_timeout": "タイムアウト（秒）",
        "chat_help_timeout": "オーケストレーターの応答待ち最大時間。エージェントの処理に時間がかかる場合は大きく設定してください。",
        "chat_btn_connection_test": "接続テスト",
        "chat_error_no_url": "オーケストレーター URL を入力してください。",
        "chat_success_connection": "接続に成功しました。",
        # guardrail
        "chat_section_guardrail": "🛡️ Guardrail",
        "chat_help_guardrail_section": (
            "AWS Bedrock Guardrail によるコンテンツフィルタリング。"
            "ユーザー入力（INPUT）と LLM レスポンス（OUTPUT）の両方を評価し、"
            "有害コンテンツや機密情報の漏洩を検知してブロックします。"
        ),
        "chat_toggle_guardrail": "Guardrail を有効にする",
        "chat_help_guardrail_toggle": (
            "ON にすると送信前（INPUT）と受信後（OUTPUT）の両タイミングで Guardrail が評価します。"
            "環境変数 BEDROCK_GUARDRAIL_ID / BEDROCK_GUARDRAIL_VERSION を設定済みの場合は自動入力されます。"
        ),
        "chat_label_guardrail_id": "Guardrail ID",
        "chat_label_guardrail_version": "バージョン",
        "chat_caption_guardrail": (
            "INPUT（送信前）と OUTPUT（受信後）の両方を評価します。\n"
            "AWS 認証情報（`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`）が必要です。"
        ),
        # steering
        "chat_section_steering": "⚖️ Steering ルール",
        "chat_section_security": "🔒 Steering & Security",
        "chat_help_steering_section": (
            "オーケストレータがエージェントの A2A 呼び出し前に実行する Guardian Agent"
            "（オーケストレータの行動を監視するエージェント）のシステムプロンプト。"
            "デフォルトは脆弱（ほぼ素通り）。本ルールを強化することで、"
            "オーケストレータに対する攻撃を Guardian Agent が検知・ブロックできます。"
        ),
        "chat_btn_steering_save": "💾 保存",
        "chat_btn_steering_reset": "↩️ リセット",
        "chat_success_steering_saved": "保存しました",
        # security tabs
        "chat_tab_llm_steering":           "LLM Steering",
        "chat_tab_agent_registry":         "エージェント認証",
        "chat_tab_task_permissions":        "タスク権限",
        # registry tab
        "chat_label_agent_name":           "エージェント名",
        "chat_label_agent_url":            "URL",
        "chat_btn_registry_add_row":       "＋ 行を追加",
        "chat_btn_registry_apply":         "適用",
        "chat_btn_registry_check":         "現在の設定を確認",
        "chat_success_security_applied":   "オーケストレーターに設定を送信しました",
        "chat_error_security_apply":       "送信失敗: {error}",
        "chat_info_registry_empty":        "エージェントが未登録です",
        # permissions tab
        "chat_label_layer2_mode":          "判定モード",
        "chat_label_layer2_keyword":       "キーワードマッチ（高速・決定論的）",
        "chat_label_layer2_llm":           "LLM 分類（高精度・非決定論的）",
        "chat_info_permissions_no_agents": "エージェント認証タブでエージェントを登録してください",
        # memory
        "chat_section_memory": "🧠 AgentCore Memory",
        "chat_help_memory_section": (
            "AWS AgentCore Memory を使用した記憶。"
            "エージェントが学習・記録したユーザー設定や会話履歴を"
            "短期記憶と長期記憶に分けて表示します。"
        ),
        "chat_btn_memory_refresh": "更新",
        "chat_btn_memory_delete": "🗑️ 全削除",
        "chat_help_memory_delete": (
            "長期記憶を削除します。短期記憶を削除したい場合は"
            "「チャット履歴をクリア」ボタンを押下してください。"
        ),
        "chat_memory_not_fetched": "未取得",
        "chat_memory_short_term": "**短期記憶**",
        "chat_memory_long_term": "**長期記憶**",
        "chat_memory_fetch_prompt": "「更新」を押して取得",
        "chat_memory_record_count_warning": "件数が多いとタイムアウトが発生する場合があります。定期的に「🗑️ 全削除」してください。",
        "chat_memory_no_events": "イベントなし",
        "chat_memory_no_records": "レコードなし",
        "chat_label_memory_strategy": "戦略",
        "chat_btn_clear_history": "🗑️ チャット履歴をクリア",
        "chat_expander_thinking": "🤔 思考過程（ターン {turn}）",
        "chat_expander_thinking_progress": "🤔 エージェントの思考過程（処理中）",
        "chat_placeholder": "メッセージを入力してチャットを開始してください",
        "chat_error_no_guardrail_id": "Guardrail ID を入力してください。",
        "chat_error_no_orchestrator": "サイドバーでオーケストレーター URL を設定してください。",
        # event rendering labels (thought process expander)
        "chat_event_steering_block":  "🚨 Steering がブロック: `{tool}`",
        "chat_event_querying_agent":  "🔧 {agent_name} に問い合わせ中",
        "chat_event_send_content":    "送信内容: 「{msg}」",
        "chat_event_calling_agent":   "🔧 エージェントを呼び出し中... *(準備中)*",
        "chat_event_tool_running":    "🔧 ツール実行中: `{tool}`",
        "chat_event_response_from":   "↩ {agent_name} からの応答",
        "chat_event_tool_result":     "↩ `{tool}` の結果",
        # sample prompts
        "chat_sample_hotel_search_label":  "🔍 ホテル検索",
        "chat_sample_hotel_search_prompt": "東京のホテルを探してください",
        "chat_sample_recommend_label":     "⭐ おすすめ",
        "chat_sample_recommend_prompt":    "おすすめのホテルを教えてください",
        "chat_sample_special_plan_label":  "🎁 特別プラン",
        "chat_sample_special_plan_prompt": "パートナー特別プランを教えてください",
        "chat_sample_review_book_label":   "📝 レビュー＋予約",
        "chat_sample_review_book_prompt":  "ハーバーグランドお台場のレビューを見て、そのまま予約して",

        # ── evaluation.py ─────────────────────────────────────────────── #
        "eval_title": "📊 Evaluation Logs",
        "eval_caption": "Langfuse に格納済みの評価スコアと会話ログを表示します。",
        "eval_header_filter": "フィルタ設定",
        "eval_label_start_date": "開始日",
        "eval_label_end_date": "終了日",
        "eval_label_limit": "取得上限 (件)",
        "eval_label_langfuse_host": "Langfuse ホスト",
        "eval_help_langfuse_host": "セルフホストの場合は変更してください。",
        "eval_btn_refresh": "データ更新",
        "eval_warning_no_data": "指定した期間にスコアデータが見つかりませんでした。期間やフィルタを変更してください。",
        "eval_label_filter": "評価観点フィルタ",
        "eval_help_filter": "表示する評価観点を選択してください。",
        "eval_metric_total_scores": "総スコア件数",
        "eval_metric_total_traces": "対象トレース数",
        "eval_metric_criteria": "評価観点数",
        "eval_subheader_timeseries": "時系列スコア推移",
        "eval_info_no_numeric": "数値スコアが見つかりませんでした。Categorical スコアは下のテーブルで確認できます。",
        "eval_subheader_logs": "会話ログ",
        "eval_col_criteria": "評価観点",
        "eval_col_score": "スコア",
        "eval_col_type": "種別",
        "eval_col_comment": "コメント",
        "eval_label_input": "**入力プロンプト**",
        "eval_label_output": "**LLM 回答**",
        "eval_error_env": "環境変数エラー: {e}",
        "eval_info_env_hint": "LANGFUSE_PUBLIC_KEY と LANGFUSE_SECRET_KEY を設定してから再試行してください。",
        "eval_error_fetch": "データ取得エラー: {e}",

        # ── visualization.py ──────────────────────────────────────────── #
        "viz_title": "🕸️ MAS Topology Visualization",
        "viz_caption": "Docker Compose（ローカル静的）または Langfuse（動的）から MAS トポロジーを可視化します。",
        "viz_header_settings": "表示設定",
        "viz_label_source": "トポロジーソース",
        "viz_option_compose": "Docker Compose (ローカル)",
        "viz_option_langfuse": "Langfuse (動的)",
        "viz_help_source": (
            "Docker Compose: リポジトリのファイル群から構成を自動解析します（Langfuse 不要）。\n"
            "Langfuse: 実行ログから動的にトポロジーを生成します。"
        ),
        "viz_label_no_physics": "物理シミュレーションを無効化",
        "viz_help_no_physics": "ON にすると静的レイアウトになり、大規模グラフで動作が安定します。",
        "viz_caption_compose_file": "解析対象ファイル:",
        "viz_caption_compose_desc": (
            "docker-compose.yml のサービス定義・環境変数・Dockerfile を静的解析して "
            "トポロジーを生成します。Langfuse は不要です。"
        ),
        "viz_subheader_langfuse": "Langfuse 取得設定",
        "viz_label_trace_limit": "取得トレース上限 (件)",
        "viz_label_hours": "過去 N 時間分を取得",
        "viz_help_hours": "0 にすると時間フィルタなし（最新 N 件のみ）",
        "viz_label_langfuse_host": "Langfuse ホスト",
        "viz_btn_refresh": "表示を更新",
        "viz_metric_components": "コンポーネント数 (ノード)",
        "viz_metric_edges": "通信経路数 (エッジ)",
        "viz_btn_download_schema": "スキーマ JSON をダウンロード",
        "viz_help_download_schema": "Threat Modeling ページでも利用できます。",
        "viz_caption_current_compose": "現在表示中: Docker Compose ローカル静的トポロジー",
        "viz_caption_current_langfuse": "現在表示中: Langfuse の動的トポロジー",
        "viz_subheader_topology": "コンポーネントトポロジー",
        "viz_caption_topology": "ノードをドラッグして移動、スクロールでズーム、ホバーで詳細を確認できます。",
        "viz_expander_traces": "取得トレース一覧 ({count} 件)",
        "viz_col_timestamp": "タイムスタンプ",
        "viz_col_trace_id": "Trace ID",
        "viz_col_trace_name": "トレース名",
        "viz_info_no_traces": "トレース情報がありません。",
        "viz_warning_no_compose": "docker-compose.yml から描画できるコンポーネントが見つかりませんでした。",
        "viz_warning_no_langfuse": "指定した条件でトレースが見つかりませんでした。取得件数・時間範囲・Langfuse ホストを確認してください。",
        "viz_summary_label_compose": "サービス数 (静的)",
        "viz_summary_label_langfuse": "取得トレース数",
        "viz_error_no_compose": "docker-compose.yml が見つかりません: {path}",
        "viz_error_compose": "Docker Compose トポロジー生成エラー: {e}",
        "viz_error_env": "環境変数エラー: {e}",
        "viz_info_env_hint": "LANGFUSE_PUBLIC_KEY と LANGFUSE_SECRET_KEY を設定してから再試行してください。",
        "viz_error_graph": "グラフ生成エラー: {e}",

        # ── threat_modeling.py ────────────────────────────────────────── #
        "tm_title": "🛡️ Threat Modeling",
        "tm_caption": "OWASP Agentic AI ガイドラインに基づく机上脅威モデリングを実施します。",
        "tm_subheader_running": "実行中...",
        "tm_progress_text": "フェーズ {completed} / {total} 完了",
        "tm_caption_running": "各フェーズで LLM が脅威評価を実施しています。完了まで数分かかります。",
        "tm_subheader_report": "脅威モデリングレポート",
        "tm_btn_download_report": "レポートをダウンロード",
        "tm_btn_rerun": "もう一度実行する",
        "tm_subheader_schema_source": "① スキーマソースの選択",
        "tm_option_viz": "Visualization の結果を使用",
        "tm_option_upload": "JSON ファイルをアップロード",
        "tm_option_text": "テキストで直接記述",
        "tm_warning_no_viz": "Visualization ページでグラフを生成してからこのオプションを使用してください。",
        "tm_success_viz_loaded": "Visualization ページで生成したスキーマを読み込みました。",
        "tm_label_upload": "system_schema.json をアップロード",
        "tm_help_upload": "visualize_traces.py --export-schema で生成した JSON ファイルを使用できます。",
        "tm_info_upload_prompt": "JSON ファイルをアップロードしてください。",
        "tm_success_upload": "JSON ファイルを読み込みました。",
        "tm_error_json_parse": "JSON のパースに失敗しました: {e}",
        "tm_label_text_input": "システムのアーキテクチャ記述",
        "tm_info_text_prompt": "システムのアーキテクチャ記述を入力してください。",
        "tm_subheader_supplement": "② 補足情報の入力",
        "tm_caption_supplement": "ログから取得できなかった項目を入力してください。入力しない項目は「情報なし」として脅威モデリングを実施します。",
        "tm_expander_detected": "ログから検出済みの情報を確認",
        "tm_success_all_detected": "全フィールドがログから検出されました。補足入力は不要です。",
        "tm_metric_agents": "検出エージェント数",
        "tm_metric_mcp_servers": "検出 MCP サーバー数",
        "tm_warning_no_agents": "エージェントが 0 件として検出されました。system_schema.json の components.agents / orchestrators / a2a_agents を確認してください。",
        "tm_subheader_output": "③ 出力形式",
        "tm_label_report_format": "レポート形式",
        "tm_btn_run": "脅威モデリングを実行",
        "tm_error_no_model_id": "環境変数 AWS_BEDROCK_MODEL_ID が設定されていません。",
        "tm_warning_empty_schema": "システム記述が空です。スキーマソースを確認してください。",
        "tm_error_run": "実行エラー: {e}",
        # bool options
        "tm_bool_none": "入力しない（情報なし）",
        "tm_bool_yes": "あり",
        "tm_bool_no": "なし",
        # memory fields
        "tm_section_memory": "記憶機構",
        "tm_field_stm": "短期記憶（セッション内）",
        "tm_field_ltm": "長期記憶（永続化）",
        "tm_field_vector_db": "ベクトル DB / RAG の使用",
        "tm_field_shared_memory": "共有メモリ（マルチエージェント・ユーザー間）",
        # tool fields
        "tm_section_tools": "ツール・実行能力",
        "tm_field_code_exec": "コード生成・実行",
        "tm_field_file_access": "ファイルシステムアクセス",
        "tm_field_messaging": "メール・メッセージ送信",
        "tm_field_db_write": "DB 書き込み",
        # auth fields
        "tm_section_auth": "認証・認可",
        "tm_field_auth_enabled": "認証機能の有効化",
        "tm_field_auth_method": "認証方式（例: JWT, OAuth2, API Key）",
        "tm_field_rbac": "RBAC（ロールベースアクセス制御）",
        "tm_field_nhi": "非人間 ID (NHI) の使用",
        "tm_field_least_priv": "最小権限原則の適用",
        "tm_field_token_rotation": "トークンローテーション",
        # human fields
        "tm_section_human": "人間の関与",
        "tm_field_hitl": "Human-in-the-Loop (HITL)",
        "tm_field_user_interaction": "ユーザー直接インタラクション",
        "tm_field_interaction_type": "インタラクション形式（例: チャット, フォーム）",
        "tm_field_trust_level": "ユーザー信頼レベル",
        # communication fields
        "tm_section_comms": "通信セキュリティ",
        "tm_field_tls": "通信暗号化（TLS/HTTPS）",
        # multi-agent fields
        "tm_section_multi_agent": "マルチエージェント構成",
        "tm_field_trust_boundary": "信頼境界の設定（例: エージェント間認証あり）",
    },

    # ------------------------------------------------------------------ #
    # English
    # ------------------------------------------------------------------ #
    "English": {
        # app.py
        "page_title": "MAS AgentCore Dashboard",
        "nav_chat": "Agent Chat",
        "nav_evaluation": "Evaluation Logs",
        "nav_visualization": "Visualization",
        "nav_threat_modeling": "Threat Modeling",

        # ── chat.py ──────────────────────────────────────────────────── #
        "chat_title": "💬 Broken MAS Chat",
        "chat_caption": "Chat with the local orchestrator via `/invocations`.",
        # sidebar sections
        "chat_section_connection": "📡 Connection",
        "chat_label_orchestrator_url": "Orchestrator URL",
        "chat_help_orchestrator_url": "Orchestrator endpoint URL (/invocations is appended automatically)",
        "chat_label_region": "AWS Region",
        "chat_help_region": "Required when using Guardrail",
        "chat_label_timeout": "Timeout (sec)",
        "chat_help_timeout": "Maximum wait time for orchestrator response. Increase if agent processing takes longer.",
        "chat_btn_connection_test": "Test Connection",
        "chat_error_no_url": "Please enter the Orchestrator URL.",
        "chat_success_connection": "Connection successful.",
        # guardrail
        "chat_section_guardrail": "🛡️ Guardrail",
        "chat_help_guardrail_section": (
            "Content filtering via AWS Bedrock Guardrail. "
            "Evaluates both user input (INPUT) and LLM response (OUTPUT) "
            "to detect and block harmful content or sensitive data leakage."
        ),
        "chat_toggle_guardrail": "Enable Guardrail",
        "chat_help_guardrail_toggle": (
            "When ON, Guardrail evaluates at both pre-send (INPUT) and post-receive (OUTPUT). "
            "If BEDROCK_GUARDRAIL_ID / BEDROCK_GUARDRAIL_VERSION env vars are set, they are pre-filled."
        ),
        "chat_label_guardrail_id": "Guardrail ID",
        "chat_label_guardrail_version": "Version",
        "chat_caption_guardrail": (
            "Evaluates both INPUT (before send) and OUTPUT (after receive).\n"
            "AWS credentials (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`) are required."
        ),
        # steering
        "chat_section_steering": "⚖️ Steering Rules",
        "chat_section_security": "🔒 Steering & Security",
        "chat_help_steering_section": (
            "System prompt for the Guardian Agent that runs before the orchestrator makes A2A calls "
            "(an agent that monitors orchestrator behavior). "
            "Default is vulnerable (mostly pass-through). "
            "Strengthening this rule enables the Guardian Agent to detect and block attacks against the orchestrator."
        ),
        "chat_btn_steering_save": "💾 Save",
        "chat_btn_steering_reset": "↩️ Reset",
        "chat_success_steering_saved": "Saved.",
        # security tabs
        "chat_tab_llm_steering":           "LLM Steering",
        "chat_tab_agent_registry":         "Agent Registry",
        "chat_tab_task_permissions":        "Task Permissions",
        # registry tab
        "chat_label_agent_name":           "Agent Name",
        "chat_label_agent_url":            "URL",
        "chat_btn_registry_add_row":       "+ Add Row",
        "chat_btn_registry_apply":         "Apply",
        "chat_btn_registry_check":         "Check Current Config",
        "chat_success_security_applied":   "Configuration sent to orchestrator.",
        "chat_error_security_apply":       "Failed to send: {error}",
        "chat_info_registry_empty":        "No agents registered.",
        # permissions tab
        "chat_label_layer2_mode":          "Detection Mode",
        "chat_label_layer2_keyword":       "Keyword Match (fast, deterministic)",
        "chat_label_layer2_llm":           "LLM Classification (accurate, non-deterministic)",
        "chat_info_permissions_no_agents": "Register agents in the Agent Registry tab first.",
        # memory
        "chat_section_memory": "🧠 AgentCore Memory",
        "chat_help_memory_section": (
            "Memory powered by AWS AgentCore Memory. "
            "Displays user preferences and conversation history learned by the agent, "
            "separated into short-term and long-term memory."
        ),
        "chat_btn_memory_refresh": "Refresh",
        "chat_btn_memory_delete": "🗑️ Delete All",
        "chat_help_memory_delete": (
            "Deletes long-term memory records. "
            "To clear short-term memory, use the 'Clear Chat History' button."
        ),
        "chat_memory_not_fetched": "Not fetched",
        "chat_memory_short_term": "**Short-term Memory**",
        "chat_memory_long_term": "**Long-term Memory**",
        "chat_memory_fetch_prompt": "Press 'Refresh' to load",
        "chat_memory_record_count_warning": "A large number of records may cause timeouts. Periodically use '🗑️ Delete All' to clear them.",
        "chat_memory_no_events": "No events",
        "chat_memory_no_records": "No records",
        "chat_label_memory_strategy": "Strategy",
        "chat_btn_clear_history": "🗑️ Clear Chat History",
        "chat_expander_thinking": "🤔 Thought Process (Turn {turn})",
        "chat_expander_thinking_progress": "🤔 Agent Thought Process (Processing...)",
        "chat_placeholder": "Type a message to start chatting",
        "chat_error_no_guardrail_id": "Please enter a Guardrail ID.",
        "chat_error_no_orchestrator": "Please set the Orchestrator URL in the sidebar.",
        # event rendering labels (thought process expander)
        "chat_event_steering_block":  "🚨 Steering blocked: `{tool}`",
        "chat_event_querying_agent":  "🔧 Querying {agent_name}",
        "chat_event_send_content":    'Content: "{msg}"',
        "chat_event_calling_agent":   "🔧 Calling agent... *(preparing)*",
        "chat_event_tool_running":    "🔧 Running tool: `{tool}`",
        "chat_event_response_from":   "↩ Response from {agent_name}",
        "chat_event_tool_result":     "↩ Result of `{tool}`",
        # sample prompts
        "chat_sample_hotel_search_label":  "🔍 Hotel Search",
        "chat_sample_hotel_search_prompt": "Please find hotels in Tokyo",
        "chat_sample_recommend_label":     "⭐ Recommended",
        "chat_sample_recommend_prompt":    "Please recommend some hotels",
        "chat_sample_special_plan_label":  "🎁 Special Plan",
        "chat_sample_special_plan_prompt": "Please tell me about partner special plans",
        "chat_sample_review_book_label":   "📝 Review + Book",
        "chat_sample_review_book_prompt":  "Show me reviews of Harbor Grand Odaiba and then book it",

        # ── evaluation.py ─────────────────────────────────────────────── #
        "eval_title": "📊 Evaluation Logs",
        "eval_caption": "Displays evaluation scores and conversation logs stored in Langfuse.",
        "eval_header_filter": "Filter Settings",
        "eval_label_start_date": "Start Date",
        "eval_label_end_date": "End Date",
        "eval_label_limit": "Fetch Limit",
        "eval_label_langfuse_host": "Langfuse Host",
        "eval_help_langfuse_host": "Change this if you are self-hosting Langfuse.",
        "eval_btn_refresh": "Refresh Data",
        "eval_warning_no_data": "No score data found for the specified period. Try adjusting the date range or filters.",
        "eval_label_filter": "Criteria Filter",
        "eval_help_filter": "Select evaluation criteria to display.",
        "eval_metric_total_scores": "Total Scores",
        "eval_metric_total_traces": "Traces",
        "eval_metric_criteria": "Criteria",
        "eval_subheader_timeseries": "Score Time Series",
        "eval_info_no_numeric": "No numeric scores found. Categorical scores are available in the table below.",
        "eval_subheader_logs": "Conversation Logs",
        "eval_col_criteria": "Criteria",
        "eval_col_score": "Score",
        "eval_col_type": "Type",
        "eval_col_comment": "Comment",
        "eval_label_input": "**Input Prompt**",
        "eval_label_output": "**LLM Response**",
        "eval_error_env": "Environment variable error: {e}",
        "eval_info_env_hint": "Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY, then retry.",
        "eval_error_fetch": "Data fetch error: {e}",

        # ── visualization.py ──────────────────────────────────────────── #
        "viz_title": "🕸️ MAS Topology Visualization",
        "viz_caption": "Visualize MAS topology from Docker Compose (static) or Langfuse (dynamic).",
        "viz_header_settings": "Display Settings",
        "viz_label_source": "Topology Source",
        "viz_option_compose": "Docker Compose (Local)",
        "viz_option_langfuse": "Langfuse (Dynamic)",
        "viz_help_source": (
            "Docker Compose: Automatically analyzes repository files (no Langfuse needed).\n"
            "Langfuse: Dynamically generates topology from execution logs."
        ),
        "viz_label_no_physics": "Disable Physics Simulation",
        "viz_help_no_physics": "Enables static layout, which improves stability for large graphs.",
        "viz_caption_compose_file": "Target file:",
        "viz_caption_compose_desc": (
            "Generates topology by statically analyzing docker-compose.yml "
            "service definitions, environment variables, and Dockerfiles. Langfuse is not required."
        ),
        "viz_subheader_langfuse": "Langfuse Settings",
        "viz_label_trace_limit": "Trace Fetch Limit",
        "viz_label_hours": "Fetch Last N Hours",
        "viz_help_hours": "Set to 0 for no time filter (latest N traces only)",
        "viz_label_langfuse_host": "Langfuse Host",
        "viz_btn_refresh": "Refresh",
        "viz_metric_components": "Components (Nodes)",
        "viz_metric_edges": "Communication Paths (Edges)",
        "viz_btn_download_schema": "Download Schema JSON",
        "viz_help_download_schema": "Can also be used on the Threat Modeling page.",
        "viz_caption_current_compose": "Currently showing: Docker Compose static topology",
        "viz_caption_current_langfuse": "Currently showing: Langfuse dynamic topology",
        "viz_subheader_topology": "Component Topology",
        "viz_caption_topology": "Drag nodes to move, scroll to zoom, hover for details.",
        "viz_expander_traces": "Trace List ({count} traces)",
        "viz_col_timestamp": "Timestamp",
        "viz_col_trace_id": "Trace ID",
        "viz_col_trace_name": "Trace Name",
        "viz_info_no_traces": "No trace information available.",
        "viz_warning_no_compose": "No renderable components found in docker-compose.yml.",
        "viz_warning_no_langfuse": "No traces found for the specified criteria. Check limit, time range, and Langfuse host.",
        "viz_summary_label_compose": "Services (Static)",
        "viz_summary_label_langfuse": "Traces Fetched",
        "viz_error_no_compose": "docker-compose.yml not found: {path}",
        "viz_error_compose": "Docker Compose topology generation error: {e}",
        "viz_error_env": "Environment variable error: {e}",
        "viz_info_env_hint": "Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY, then retry.",
        "viz_error_graph": "Graph generation error: {e}",

        # ── threat_modeling.py ────────────────────────────────────────── #
        "tm_title": "🛡️ Threat Modeling",
        "tm_caption": "Tabletop threat modeling based on the OWASP Agentic AI guidelines.",
        "tm_subheader_running": "Running...",
        "tm_progress_text": "Phase {completed} / {total} complete",
        "tm_caption_running": "LLM is evaluating threats for each phase. This may take a few minutes.",
        "tm_subheader_report": "Threat Modeling Report",
        "tm_btn_download_report": "Download Report",
        "tm_btn_rerun": "Run Again",
        "tm_subheader_schema_source": "① Schema Source",
        "tm_option_viz": "Use Visualization result",
        "tm_option_upload": "Upload JSON file",
        "tm_option_text": "Enter text directly",
        "tm_warning_no_viz": "Please generate a graph on the Visualization page before using this option.",
        "tm_success_viz_loaded": "Schema loaded from the Visualization page.",
        "tm_label_upload": "Upload system_schema.json",
        "tm_help_upload": "You can use a JSON file generated by visualize_traces.py --export-schema.",
        "tm_info_upload_prompt": "Please upload a JSON file.",
        "tm_success_upload": "JSON file loaded.",
        "tm_error_json_parse": "Failed to parse JSON: {e}",
        "tm_label_text_input": "System Architecture Description",
        "tm_info_text_prompt": "Please enter a system architecture description.",
        "tm_subheader_supplement": "② Supplemental Information",
        "tm_caption_supplement": "Enter items that could not be retrieved from logs. Items left blank will be treated as 'no information'.",
        "tm_expander_detected": "Review detected information from logs",
        "tm_success_all_detected": "All fields were detected from logs. No supplemental input needed.",
        "tm_metric_agents": "Detected Agents",
        "tm_metric_mcp_servers": "Detected MCP Servers",
        "tm_warning_no_agents": "0 agents detected. Check components.agents / orchestrators / a2a_agents in system_schema.json.",
        "tm_subheader_output": "③ Output Format",
        "tm_label_report_format": "Report Format",
        "tm_btn_run": "Run Threat Modeling",
        "tm_error_no_model_id": "Environment variable AWS_BEDROCK_MODEL_ID is not set.",
        "tm_warning_empty_schema": "System description is empty. Check the schema source.",
        "tm_error_run": "Execution error: {e}",
        # bool options
        "tm_bool_none": "Not specified (no info)",
        "tm_bool_yes": "Yes",
        "tm_bool_no": "No",
        # memory fields
        "tm_section_memory": "Memory Mechanisms",
        "tm_field_stm": "Short-term memory (in-session)",
        "tm_field_ltm": "Long-term memory (persistent)",
        "tm_field_vector_db": "Vector DB / RAG usage",
        "tm_field_shared_memory": "Shared memory (multi-agent / cross-user)",
        # tool fields
        "tm_section_tools": "Tools & Execution Capabilities",
        "tm_field_code_exec": "Code generation / execution",
        "tm_field_file_access": "File system access",
        "tm_field_messaging": "Email / messaging",
        "tm_field_db_write": "Database write access",
        # auth fields
        "tm_section_auth": "Authentication & Authorization",
        "tm_field_auth_enabled": "Authentication enabled",
        "tm_field_auth_method": "Auth method (e.g. JWT, OAuth2, API Key)",
        "tm_field_rbac": "RBAC (Role-Based Access Control)",
        "tm_field_nhi": "Non-Human Identity (NHI) usage",
        "tm_field_least_priv": "Least privilege principle applied",
        "tm_field_token_rotation": "Token rotation",
        # human fields
        "tm_section_human": "Human Involvement",
        "tm_field_hitl": "Human-in-the-Loop (HITL)",
        "tm_field_user_interaction": "Direct user interaction",
        "tm_field_interaction_type": "Interaction type (e.g. chat, form)",
        "tm_field_trust_level": "User trust level",
        # communication fields
        "tm_section_comms": "Communication Security",
        "tm_field_tls": "Encryption (TLS/HTTPS)",
        # multi-agent fields
        "tm_section_multi_agent": "Multi-Agent Configuration",
        "tm_field_trust_boundary": "Trust boundary (e.g. inter-agent authentication)",
    },
}


def get_translations(lang: str) -> dict[str, str]:
    """指定言語の翻訳辞書を返す。未知の言語は日本語にフォールバック。"""
    return TRANSLATIONS.get(lang, TRANSLATIONS["日本語"])
