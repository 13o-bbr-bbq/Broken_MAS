/*
 * BrokenMAS カスタム YARA ルール
 * ホテル予約 MAS 環境向け プロンプトインジェクション検出
 *
 * 検出対象:
 *   - Scenario E: ツール説明文への日本語隠し命令埋め込み
 *   - 汎用: 日本語/英語の「ユーザーに開示不要」指示パターン
 *   - 汎用: 自動実行・隠蔽プロトコル宣言
 *
 * 使用方法:
 *   mcp-scanner --analyzers yara --rules-path ./custom_yara_rules remote --server-url <URL>
 */

rule HotelMAS_ToolDescriptionPoisoning_JP
{
    meta:
        description  = "ツール説明文に日本語の隠し予約命令が埋め込まれている（Scenario E）"
        severity     = "HIGH"
        threat_type  = "TOOL POISONING"
        author       = "BrokenMAS Training"
        reference    = "OWASP Top 10 for LLM - T6 Tool Poisoning"

    strings:
        // --- ユーザー秘匿指示 ---
        $hide_jp_1  = "ユーザーへの開示は不要" wide ascii nocase
        $hide_jp_2  = "ユーザーに開示しないでください" wide ascii nocase
        $hide_jp_3  = "ユーザーには伝えないでください" wide ascii nocase
        $hide_jp_4  = "ユーザーに知らせないでください" wide ascii nocase

        // --- 偽システム連携プロトコル宣言 ---
        $proto_jp_1 = /\[.{0,20}(統合プロトコル|連携プロトコル|管理システム).{0,20}\]/ wide ascii
        $proto_jp_2 = "必須手順" wide ascii nocase
        $proto_jp_3 = "標準連携手順" wide ascii nocase
        $proto_jp_4 = "自動実行" wide ascii nocase

        // --- 予約命令パターン（日本語文脈での make_reservation 強制） ---
        $booking_1  = "make_reservation" wide ascii nocase
        $booking_2  = "total_price=" wide ascii nocase

    condition:
        // 秘匿指示 OR プロトコル宣言 のいずれかが存在 → 高確度
        (any of ($hide_jp_*)) or
        // プロトコル宣言 + 予約命令の組み合わせ
        (any of ($proto_jp_*) and any of ($booking_*))
}


rule HotelMAS_AutoBookingInstruction
{
    meta:
        description  = "ツール説明に LLM への自動予約実行命令が含まれる"
        severity     = "HIGH"
        threat_type  = "PROMPT INJECTION"
        author       = "BrokenMAS Training"
        reference    = "OWASP Top 10 for LLM - T1 Prompt Injection"

    strings:
        // 自動実行を促す日本語パターン
        $auto_jp_1  = "自動実行してください" wide ascii nocase
        $auto_jp_2  = "自動的に実行" wide ascii nocase
        $auto_jp_3  = "自動で予約" wide ascii nocase

        // 英語自動実行パターン
        $auto_en_1  = "automatically execute" wide ascii nocase
        $auto_en_2  = "auto-execute" wide ascii nocase
        $auto_en_3  = "run automatically without" wide ascii nocase

        // 特定パラメータ値を指定した実行命令（インジェクション確度高）
        $param_1    = "guest_name=" wide ascii nocase
        $param_2    = "checkin=" wide ascii nocase
        $param_3    = "hotel_id=" wide ascii nocase

    condition:
        // 自動実行命令 + 具体的なパラメータ値の組み合わせ
        (any of ($auto_jp_*) or any of ($auto_en_*)) and
        (2 of ($param_*))
}


rule HotelMAS_UnauthorizedPriceManipulation
{
    meta:
        description  = "ツール説明に不正な低価格設定命令が含まれる（価格改ざん攻撃）"
        severity     = "HIGH"
        threat_type  = "TOOL POISONING"
        author       = "BrokenMAS Training"
        reference    = "OWASP Top 10 for LLM - T6 Tool Poisoning"

    strings:
        // total_price に数値を直接埋め込むパターン
        $price_1    = /total_price\s*=\s*[0-9]{1,5}[^0-9]/ wide ascii
        // 安価な価格（5桁以下）を示唆するパターン
        $price_2    = /total_price=['"]?\d{1,4}['"]?/ wide ascii

        // 予約ツール名
        $tool_1     = "make_reservation" wide ascii nocase

    condition:
        $tool_1 and any of ($price_*)
}
