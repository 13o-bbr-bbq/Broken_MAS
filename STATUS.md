# STATUS — Broken MAS

_最終更新: 2026-07-02 (このセッション)_

## 現在地（今どのマイルストーン上か）
MAS（AI ホテル予約アシスタント）本体・ダッシュボード・攻撃シナリオ A〜E は実装済みで稼働中。Bedrock / Ollama のプロバイダ切り替えも導入済み。現在はドキュメント整備フェーズ。

## 進捗（マイルストーン / フェーズの達成状況）
- ✅ MAS コンポーネント（Orchestrator / A2A Agent 1-3 / MCP Server 1-6）
- ✅ SecureSteeringHandler（3層防御）+ `POST /security-config` ホットリロード
- ✅ 攻撃シナリオ A〜E（間接PI / 経済DoS / メモリ汚染 / なりすましA2A / ツール説明文ポイズニング）
- ✅ ダッシュボード 5 ページ（Chat / Evaluation / Visualization / Threat Modeling / MCP Security Scan）
- ✅ LLM プロバイダ切り替え（`LLM_PROVIDER` で Bedrock / Ollama、`llm_factory.make_model`）
- 🟡 ドキュメント整備 — README のモデル要件補足を追加（このセッション）

## 直近やったこと（最大3件）
- README.md / README.ja.md の Ollama 注意点に「モデル要件 = ツール呼び出し＋日本語の両方必須」補足を追加（防御側日本語推論の重要性、qwen2.5 推奨を明記）

## Next Action（次の一手・実行可能な粒度で1つ）
- ローカルモデル候補（`llama3.1` / `qwen2.5`）を「①ツール呼び出しの安定性 ②防御側 Steering の日本語検知率（攻撃シナリオ A〜E の再現/検知）」の2軸で実測比較し、結果を README のモデル選定ガイドに反映する

## ブロッカー / 未決事項
- なし

## メモ（再構築コストの高い判断の理由）
- モデル要件を README に補足した根拠: ツール呼び出しは「無いと起動しない」ハード要件だが、日本語は「動くが検証の妥当性が崩れる」品質グラデーション要件、と強制力の質が異なる。特に防御側（Steering Layer 2/3）の日本語推論が弱いと攻撃見逃し（False Negative）で実験が無効化するため、Ollama では CJK に強い qwen2.5 を llama3.1 より優先推奨とした。
