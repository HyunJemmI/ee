# Automotive E/E Requirements Pipeline

자동차 문서에서 전기·전자(E/E) 관련 요구사항을 자동 추출, 분류, 번역하는 파이프라인입니다.

## 파이프라인 구조

```
M1: Document Loader      → manifest.jsonl
M2: Document Parser      → document_blocks.jsonl (heading 기반 블록 추출)
M2.5: Keyword Prescreen  → prescreen_results.jsonl (키워드 기반 파일 선별)
M3: Contextual Chunker   → structured_blocks.jsonl (heading 기반 그룹핑)
M4: Segmenter            → requirement_candidates.jsonl (세그먼트 분할)
M5: AI Classifier (ChatT)→ classifications.jsonl (2축 분류: 요구사항 여부 × 전장품 관련성)
M6: Translate (ChatT)    → translated.jsonl (영문→한문 번역)
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
python -m ee_pipeline.cli m3          # 청커
python -m ee_pipeline.cli m4          # 세그멘터
python -m ee_pipeline.cli m5          # AI 분류 (ChatT API)
python -m ee_pipeline.cli m6          # 번역 (ChatT API)
python -m ee_pipeline.cli m7_m8       # 집계 + 보고서 생성
```

## 출력 보고서 (XLSX)

| 시트 | 내용 |
|------|------|
| 문서분류 | 파일별 전장품 관련 여부 판정 |
| 요구사항분류 | 블록별 2축 분류 결과 |
| 고객사 요구사항 분석 | 최종 EE 요구사항 목록 (영문+한문) |
| 검토 절차 | 검토 절차 안내 |

## ChatT API

- **URL**: `https://chat.hyundai-transys.com/`
- **모델**: `ChatT-VIBE`
- 인증: Bearer Token (`.env` 파일 또는 `config.py`에 하드코딩)

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
        ├── module5_classifier.py
        ├── module6_translate.py
        ├── module7_aggregator.py
        └── module8_report.py
```