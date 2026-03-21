---
name: threat-modeling
description: 提供されたアーキテクチャ記述に基づき、OWASP Agentic AI Threats and Mitigations v1.1 に準拠した机上脅威モデリングを実施する
disable-model-invocation: true
argument-hint: [system_description]
---

# Threat Modeling SOP — OWASP Agentic AI v1.1

## Overview

提供されたシステムアーキテクチャ記述を唯一の情報源として、T1〜T17 の脅威を系統的に評価し、構造化されたレポートを生成する。
外部システムへのアクセス・スキャン・プローブは一切行わない。

## Parameters

`<user-input>` ブロックから以下の値を取り出して使用すること:

- **system_description**: `system_description:` の値（対象システムの記述）
- **output_format**: `output_format:` の値（"markdown" または "json"、省略時は "markdown"）
- **session_id**: `session_id:` の値（セッション識別子）

## Steps

### Step 0: Architecture Parsing

MUST: `run_phase(phase_num=0, architecture=<system_description の値>, previous_findings="")` を呼ぶこと。
MUST: 返却された結果を `record_phase_finding(phase_num=0, phase_title="Architecture Parsing", findings=<結果>, session_id=<session_id の値>)` で記録してから Step 1 へ進むこと。

### Step 1: Planning & Reasoning Threats (T6, T7, T8)

MUST: `run_phase(phase_num=1, architecture=<system_description の値>, previous_findings=<Step0結果>)` を呼ぶこと。
MUST: 返却された結果を `record_phase_finding(phase_num=1, phase_title="Planning & Reasoning Threats", findings=<結果>, session_id=<session_id の値>)` で記録してから Step 2 へ進むこと。

### Step 2: Memory Threats (T1, T5)

MUST: `run_phase(phase_num=2, architecture=<system_description の値>, previous_findings=<Step0〜1の結果サマリー>)` を呼ぶこと。
MUST: `record_phase_finding(phase_num=2, phase_title="Memory Threats", findings=<結果>, session_id=<session_id の値>)` で記録してから Step 3 へ進むこと。

### Step 3: Tool & Execution Threats (T2, T4, T11, T17)

MUST: `run_phase(phase_num=3, architecture=<system_description の値>, previous_findings=<Step0〜2の結果サマリー>)` を呼ぶこと。
MUST: `record_phase_finding(phase_num=3, phase_title="Tool & Execution Threats", findings=<結果>, session_id=<session_id の値>)` で記録してから Step 4 へ進むこと。

### Step 4: Authentication & Identity Threats (T3, T9)

MUST: `run_phase(phase_num=4, architecture=<system_description の値>, previous_findings=<Step0〜3の結果サマリー>)` を呼ぶこと。
MUST: `record_phase_finding(phase_num=4, phase_title="Authentication & Identity Threats", findings=<結果>, session_id=<session_id の値>)` で記録してから Step 5 へ進むこと。

### Step 5: Human Interaction Threats (T10, T15)

MUST: `run_phase(phase_num=5, architecture=<system_description の値>, previous_findings=<Step0〜4の結果サマリー>)` を呼ぶこと。
MUST: `record_phase_finding(phase_num=5, phase_title="Human Interaction Threats", findings=<結果>, session_id=<session_id の値>)` で記録してから Step 6 へ進むこと。

### Step 6: Multi-Agent System Threats (T12, T13, T14, T16)

MUST: `run_phase(phase_num=6, architecture=<system_description の値>, previous_findings=<Step0〜5の結果サマリー>)` を呼ぶこと。
MUST: `record_phase_finding(phase_num=6, phase_title="Multi-Agent System Threats", findings=<結果>, session_id=<session_id の値>)` で記録してから Step 7 へ進むこと。

### Step 7: Report Generation

MUST: `generate_threat_report(session_id=<session_id の値>, output_format=<output_format の値>)` を呼んで最終レポートを生成すること。
MUST NOT: 記憶から結果を再構成してはならない。保存済みデータのみを使用すること。
MUST NOT: generate_threat_report を呼ばずにテキストのみでレポートを出力してはならない。
