# Automotive E/E Requirements Pipeline

자동차 문서에서 전기·전자(E/E) 관련 요구사항을 자동 추출, 분류, 번역하는 파이프라인입니다.

## 파이프라인 구조

```
M1: Document Loader      → manifest.jsonl
M2: Document Parser      → document_blocks.jsonl (heading 기반 블록 추출)
M2.5: Keyword Prescreen  → prescreen_results.jsonl (키워드 기반 파일 선별)
M3: Contextual Chunker   → structured_blocks.jsonl (heading 기반 그룹핑)
M4: Segmenter            → requirement_batches.jsonl (문맥 배치 생성)
M4.5: LLM Input Adapter  → llm_inputs.jsonl (원자 단위 분리 + 안정적인 cur_id)
M5: AI Classifier (ChatT) → classifications.jsonl (2축 분류: 요구사항 여부 × 전장품 관련성)
M6: Translate (ChatT)     → translated.jsonl (영문→한글 번역)
M7: Aggregator           → spec_records.jsonl (결과 통합)
M8: XLSX Report          → final_report.xlsx (최종 보고서)
```

## 빠른 시작

```bash
# 1. 의존성 설치
pip install -e ".[dev]"

# 2. 입력 파일 배치
# data/input/ 디렉토리에 PDF, DOCX, PPTX, XLSX 파일을 넣습니다.

# 3. 전체 파이프라인 실행
python -m ee_pipeline.cli run

# 또는 개별 모듈 실행
python -m ee_pipeline.cli m1          # 문서 로더
python -m ee_pipeline.cli m2          # 문서 파서
python -m ee_pipeline.cli m25         # 키워드 선별
python -m ee_pipeline.cli m3          # 컨텍스트 청커
python -m ee_pipeline.cli m4          # 요구사항 세그멘터
python -m ee_pipeline.cli m45         # LLM 입력 정규화 + cur_id 생성
python -m ee_pipeline.cli m5          # AI 분류 (ChatT)
python -m ee_pipeline.cli m6          # 번역 (ChatT)
python -m ee_pipeline.cli m7-m8       # 집계 + 보고서 생성
```

### Windows (Anaconda Prompt / PowerShell)

```powershell
conda create -n elecreq python=3.11 -y
conda activate elecreq
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env

# .env에 ChatT 발급값을 입력한 다음 실행
python -m ee_pipeline.cli run --input "data\input" --output "output"
```

`cmd.exe`에서는 `Copy-Item` 대신 `copy .env.example .env`를 사용합니다. 경로 처리는
`pathlib` 기반이므로 `/`와 `\`를 코드에 직접 조합할 필요가 없습니다.

## 출력 보고서 (XLSX)

| 시트 | 내용 |
|------|------|
| 문서 요약 | 문서별 전장품 관련 여부, 전체 요구사항 수, 전장품 요구사항 수 |
| 전장 요구사항 | doc_id, cur_id, 원문, 한글 번역, 비고, 전장품 종류 |

## ChatT API

프로젝트 루트의 `.env.example`을 `.env`로 복사하고 ChatT 관리자가 발급한 값을 입력합니다.

```bash
cp .env.example .env
```

- **API key**: `CHATT_API_KEY`
- **주소**: `CHATT_BASE_URL` (서버 루트, `/v1`, 전체 `/v1/chat/completions` 주소 모두 허용)
- **모델**: `CHATT_MODEL`
- **동시 호출**: `CHATT_MAX_CONCURRENCY` (기본값 `4`)
- **분당 호출**: `CHATT_REQUESTS_PER_MINUTE` (기본값 `60`, 실제 서비스 한도 이하로 설정)

기본 동시 호출 수는 안정성을 위해 4다. ChatT 관리자가 동시 요청 8개를 명시적으로 허용하고 실제 입력으로 안정성 검증을 마친 경우에만 8로 올리는 것을 권장한다. 필수 설정이 없으면 M5 시작 전에 명확한 오류로 중단되며, API 실패를 비요구사항으로 저장하지 않는다.

## 키워드 기반 선별

첨부 8번 문서(`일반 전장품 정의 및 영어 키워드.html`)의 키워드를 사용하여:
- **1차 키워드**: 문서 수준 선별 (8 카테고리, 60개 키워드)
- **2차 키워드**: 요구사항 수준 선별 (7 카테고리, 107개 키워드)

## 파일 구조

```
EE-req/
├── .env
├── pyproject.toml
├── README.md
├── data/
│   ├── keywords/
│   │   └── ee_keywords.json
│   └── input/          ← 입력 파일 배치
├── output/             ← 파이프라인 출력
│   ├── manifest.jsonl
│   ├── document_blocks.jsonl
│   ├── prescreen_results.jsonl
│   ├── structured_blocks.jsonl
│   ├── requirement_batches.jsonl
│   ├── skipped_candidates.jsonl
│   ├── llm_inputs.jsonl
│   ├── classifications.jsonl
│   ├── translated.jsonl
│   ├── spec_records.jsonl
│   └── final_report.xlsx
└── src/ee_pipeline/
    ├── __init__.py
    ├── config.py
    ├── models.py
    ├── cli.py
    ├── orchestrator.py
    └── modules/
        ├── module1_loader.py
        ├── module2_parser.py
        ├── module2_5_prescreen.py
        ├── module3_chunker.py
        ├── module4_segmenter.py
        ├── module4_5_llm_input.py
        ├── module5_classifier.py
        ├── module6_translate.py
        ├── module7_aggregator.py
        └── module8_report.py
```

## 구현 및 검증 문서

- [최종 구현 요약](PROJECT_FINAL_REPORT.md)
- [M1~M4.5 개선 보고서](m2_m4_improvement_report.md)
- [M2~M4 closed-loop 검증](m2_m4_closed_loop_report.md)
- [발표용 구현 보고서](presentation_implementation_report.md)
- [Windows·ChatT 검증 보고서](windows_chatt_validation_report.md)
