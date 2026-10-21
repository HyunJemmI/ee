# Automotive E/E Requirements Pipeline 최종 구현 요약

작성일: 2026-10-21

## 1. 목표와 최종 구조

PDF, DOCX, PPTX, XLSX, XLS 문서에서 요구사항을 추출하고 전체 요구사항과 전장품 관련 요구사항을 구분한 뒤, 번역과 원문 추적 정보를 포함한 XLSX 보고서를 생성한다.

```text
M1    문서 탐색             → manifest.jsonl
M2    형식별 구조 파싱       → document_blocks.jsonl
M2.5  키워드 증거 수집       → prescreen_results.jsonl
M3    문맥 청킹             → structured_blocks.jsonl
M4    요구사항 후보 분할     → requirement_batches.jsonl
M4.5  LLM 입력 원자화       → llm_inputs.jsonl
M5    요구사항/E/E 2축 분류 → classifications.jsonl
M6    E/E 요구사항 번역      → translated.jsonl
M7    결과와 출처 통합       → spec_records.jsonl
M8    최종 2개 시트 보고서   → final_report_*.xlsx
```

M2.5는 삭제 필터가 아니라 참고 증거 단계다. 모든 파싱 블록을 M3 이후 단계로 보내므로 비전장 요구사항도 M5의 `전체 요구사항` 분모에 포함될 수 있다.

## 2. 핵심 구현 내용

### 문서 파싱과 문맥 유지

- 동일 파일명의 `doc_id` 충돌을 방지하고 입력 파일을 결정론적으로 정렬한다.
- PDF는 페이지, 제목, 표, 반복 헤더·푸터, 그림과 출처 좌표를 분리한다.
- DOCX의 제목 계층과 표 행, PPTX의 슬라이드·표, XLSX/XLS의 시트·행을 원자 단위로 보존한다.
- `block_type`과 `section_role`을 분리해 물리적 표 구조와 메타데이터 의미를 동시에 유지한다.
- 문서, 페이지, 슬라이드, 시트 행 경계를 넘는 잘못된 병합을 방지한다.

### 요구사항 후보와 M4.5

- M4는 그림, 페이지 번호, 반복 레이아웃, 법적 문구 등을 별도 제외 후보로 기록한다.
- 목차의 `contents`와 본문의 동사 `contents`를 구분하도록 메타데이터 판정을 제목 전체 일치 방식으로 변경했다.
- M4.5는 표를 사양 행 단위로 분리하고 문서별 안정적인 `cur_id`를 만든다.
- LLM에는 문서, 제목, 페이지, 표 문맥과 원문을 함께 제공하되 최종 Sheet 2에는 합성 프롬프트가 아닌 원문을 보존한다.

### ChatT 연동

- OpenAI SDK 종속성을 제거하고 `httpx` 기반 ChatT 어댑터로 통합했다.
- API key, 주소, 모델, 동시 호출 수, RPM은 `.env`에서 읽는다.
- 기본 동시 호출 수는 4이며 전역 요청 pacer로 RPM을 제한한다.
- HTTP 오류와 rate limit은 재시도하고, 최종 실패를 `non-REQ/non-EE`로 저장하지 않는다.
- M5 응답의 boolean, E/E category, 필수 필드를 검증해 모호한 모델 출력을 차단한다.
- M6는 `요구사항 AND E/E 관련` 교집합만 번역한다.

### 최종 집계와 보고서

- M7은 분류·번역·원문 provenance를 `SpecRecord`로 통합한다.
- 누락, 빈 번역, `REVIEW_NEEDED`, 중복 번역 ID가 있으면 완성 보고서를 만들지 않는다.
- M8은 Sheet 1 집계의 E/E 요구사항 집합과 Sheet 2 상세 행을 대조한다.
- Sheet 1은 문서별 전장품 관련 여부, 전체 요구사항 수, 전장품 요구사항 수와 비율을 제공한다.
- Sheet 2는 `doc_id / cur_id / 원문 / 한글 번역 / 비고 / 전장품 종류`를 제공한다.

## 3. 실제 문서 검증 결과

대형 PDF를 제외하고 CAN, PMIC, 자기식 센서 데이터시트 3개, 총 74페이지로 M1~M4.5를 실행했다.

| 항목 | 결과 |
|---|---:|
| 문서 / 고유 doc_id | 3 / 3 |
| 페이지 커버리지 | 74/74 (100%) |
| M2 파싱 블록 / 빈 블록 | 2,153 / 0 |
| M3 청크 | 111 |
| 문서 경계 오병합 / provenance 누락 | 0 / 0 |
| M4 처리 배치 / 제외 후보 | 344 / 415 |
| M4.5 원자 LLM 입력 | 581 |
| 중복 batch_id / cur_id / 빈 원문 | 0 / 0 / 0 |

PDF 외 형식은 자동 테스트에서 DOCX 제목·표, PPTX 슬라이드·표, XLSX 요구사항 행·지원 시트를 검증했다. XLS 파서는 `pandas`와 `xlrd`로 구현돼 있지만 실제 XLS 표본 검증은 남아 있다.

## 4. 코드 품질과 호환성

최종 검사 결과:

```text
pytest -q              26 passed
ruff check src tests   All checks passed
python -m pip check    No broken requirements found
git diff --check       통과
```

- Python 3.11 이상을 사용한다.
- 파일과 디렉터리는 `pathlib`로 처리해 Windows 경로에 종속되지 않는다.
- PDF는 `import pymupdf`와 `pymupdf>=1.24.3`을 사용해 별도 `fitz` 패키지 충돌을 방지한다.
- XLSX 생성은 `openpyxl`을 사용하므로 Microsoft Excel 설치가 필요하지 않다.
- API key와 실제 입력·출력 파일은 Git에 포함하지 않는다.

## 5. Windows 실행

```powershell
conda create -n elecreq python=3.11 -y
conda activate elecreq
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

`.env`에 발급값을 넣는다.

```dotenv
CHATT_API_KEY=발급받은_API_KEY
CHATT_BASE_URL=https://실제_서버_주소
CHATT_MODEL=실제_모델명
CHATT_MAX_CONCURRENCY=4
CHATT_REQUESTS_PER_MINUTE=실제_서비스_한도
```

```powershell
python -m ee_pipeline.cli run --input "data\input" --output "output"
```

## 6. API 교체 가능 범위

ChatT가 다음 OpenAI 호환 계약을 제공하면 `.env` 값만 입력하면 된다.

1. `POST .../chat/completions`
2. `Authorization: Bearer <API key>`
3. `model`, `messages`, `temperature`, `max_tokens` 요청 필드
4. `choices[0].message.content` 응답 구조

주소는 서버 루트, `/v1`, 완전한 `.../chat/completions` endpoint를 모두 허용한다. 인증 헤더나 응답 구조가 다르면 `src/ee_pipeline/chatt_client.py`만 수정하면 된다.

실제 ChatT key와 서버가 없어 네트워크 인증, 사내 TLS, 모델 권한과 의미 정확도는 검증하지 않았다. 운영 전 소량 smoke test가 필요하며, 정답 라벨이 없으므로 분류 정확도 90% 이상이라고 주장하지 않는다.

## 7. 관련 문서

- `README.md`: 설치, 실행, 산출물과 환경변수
- `module1-5_report.md`: 초기 모듈 구조와 평가
- `m2_m4_improvement_report.md`: M2~M4 개선 전후 비교
- `m2_m4_closed_loop_report.md`: closed-loop 검토 결과
- `presentation_implementation_report.md`: 발표용 상세 구현 자료
- `windows_chatt_validation_report.md`: Windows와 ChatT 계약 검증

실행 산출물, 입력 원본, 로컬 Ollama 검증 스크립트, 가상환경, 캐시와 빌드 메타데이터는 `.gitignore`로 제외한다.
