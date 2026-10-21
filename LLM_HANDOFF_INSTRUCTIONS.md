# 회사 환경 LLM용 인수인계 프롬프트

아래의 `복사 시작`부터 `복사 끝`까지 회사 환경의 LLM에게 그대로 전달하면 된다. 실제 API key는 프롬프트에 적지 말고 사용자가 직접 `.env`에 입력한다.

---

## 복사 시작

다음 GitHub 저장소를 Windows 회사 PC에 clone한 상태다.

```text
https://github.com/HyunJemmI/ee.git
```

이 프로젝트는 PDF, DOCX, PPTX, XLSX, XLS 문서에서 전체 요구사항과 자동차 전장품(E/E) 관련 요구사항을 구분하고 한국어로 번역한 뒤 XLSX 보고서를 생성하는 Python 파이프라인이다.

너의 목표는 **기존 M1~M4.5와 M7~M8 동작을 보존하면서 회사 ChatT API만 연결하고, 소량 검증 후 전체 파이프라인을 실행하는 것**이다.

### 반드시 먼저 읽을 파일

1. `PROJECT_FINAL_REPORT.md`
2. `README.md`
3. `windows_chatt_validation_report.md`
4. `.env.example`
5. `src/ee_pipeline/chatt_client.py`
6. `src/ee_pipeline/config.py`
7. `src/ee_pipeline/modules/module5_classifier.py`
8. `src/ee_pipeline/modules/module6_translate.py`

### 작업 원칙

- 먼저 코드를 수정하지 말고 현재 상태를 검사한다.
- `.env`와 실제 API key를 출력, 로그, 보고서, Git commit에 포함하지 않는다.
- 입력 원본과 `output*` 산출물도 Git에 추가하지 않는다.
- 문서명이나 특정 제조사에 맞춘 예외 규칙을 추가하지 않는다.
- M1~M4.5와 M7~M8은 테스트가 실패하거나 명확한 범용 버그가 발견된 경우에만 수정한다.
- ChatT API 계약이 다르면 우선 `src/ee_pipeline/chatt_client.py` 한 곳에서 차이를 흡수한다.
- API 실패를 `non-REQ` 또는 `non-EE`로 저장하지 않는다. 실패한 실행은 산출물 작성을 중단해야 한다.
- 처음부터 전체 문서를 호출하지 말고 1~3건으로 API smoke test를 통과한 다음 전체 실행한다.
- 동시 호출은 4로 시작한다. 회사 API 관리자가 8을 허용한다고 명확히 확인한 경우에만 8로 높인다.

### 1. Windows 개발 환경 준비

PowerShell 또는 Anaconda Prompt에서 실행한다.

```powershell
conda create -n elecreq python=3.11 -y
conda activate elecreq
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

`cmd.exe`라면 다음 명령을 사용한다.

```cmd
copy .env.example .env
```

### 2. ChatT 환경변수 입력

사용자가 프로젝트 루트의 `.env`에 직접 다음 값을 입력하도록 안내한다. API key의 실제 값은 네가 답변이나 로그에 다시 표시하지 않는다.

```dotenv
CHATT_API_KEY=회사에서_발급받은_API_KEY
CHATT_BASE_URL=https://실제_ChatT_서버_주소
CHATT_MODEL=실제_모델명
CHATT_MAX_CONCURRENCY=4
CHATT_REQUESTS_PER_MINUTE=회사에서_확인한_한도
```

현재 어댑터가 기대하는 기본 API 계약은 다음과 같다.

1. `POST .../chat/completions`
2. `Authorization: Bearer <API key>`
3. 요청 JSON: `model`, `messages`, `temperature`, `max_tokens`
4. 응답 JSON: `choices[0].message.content`

`CHATT_BASE_URL`에는 다음 형식을 사용할 수 있다.

- 서버 루트: `https://server.example.com`
- `/v1` 루트: `https://server.example.com/v1`
- 전체 endpoint: `https://server.example.com/custom/chat/completions`

회사 API가 다른 인증 헤더나 응답 구조를 사용하면 그 차이를 확인한 뒤 `chatt_client.py`에서만 조정한다. TLS 인증서를 비활성화하지 말고 회사 CA 인증서 적용 방법을 확인한다.

### 3. API 호출 전 코드 검증

```powershell
pytest -q
ruff check src tests
python -m pip check
python -m ee_pipeline.cli --help
```

기준 결과는 다음과 같다.

```text
pytest: 26 passed
ruff: All checks passed
pip check: No broken requirements found
```

PyMuPDF import 또는 DLL 오류가 발생하면 `fitz`라는 별도 패키지를 설치하지 않는다. `pymupdf>=1.24.3`이 설치됐는지 확인하고, Windows DLL 오류라면 Microsoft Visual C++ Redistributable을 확인한다.

### 4. 입력 문서 배치

실제 검증 문서를 다음 폴더에 넣는다.

```text
data/input/
```

지원 확장자는 다음과 같다.

```text
.pdf .docx .pptx .xlsx .xls
```

처음에는 작은 문서 1~3개만 사용한다. 대형 PDF는 smoke test가 끝난 후 실행한다.

### 5. M1~M4.5 선행 검증

API를 호출하지 않고 다음 단계까지만 먼저 실행한다.

```powershell
python -m ee_pipeline.cli m1 --input "data\input" --output "output_check"
python -m ee_pipeline.cli m2 --input "data\input" --output "output_check"
python -m ee_pipeline.cli m25 --output "output_check"
python -m ee_pipeline.cli m3 --output "output_check"
python -m ee_pipeline.cli m4 --output "output_check"
python -m ee_pipeline.cli m45 --output "output_check"
```

다음을 확인한다.

- `manifest.jsonl`의 모든 입력 파일이 고유 `doc_id`를 갖는가
- `document_blocks.jsonl`에 빈 원문이 없는가
- 페이지, 슬라이드, 시트 행과 `source_file` 정보가 보존되는가
- `structured_blocks.jsonl`이 서로 다른 문서를 합치지 않는가
- `requirement_batches.jsonl`에서 그림·레이아웃·법적 문구가 처리 후보와 구분되는가
- `llm_inputs.jsonl`의 `batch_id`, `cur_id`가 중복되지 않는가
- `original_text`와 `llm_text`가 비어 있지 않은가

문제 발견 시 구체적인 입력·출력 레코드와 원문 근거를 먼저 제시하고, 특정 파일명 기반 규칙이 아닌 문서 구조의 근본 원인을 수정한다.

### 6. ChatT smoke test

전체 581건과 같은 대량 호출 전에 `output_check/llm_inputs.jsonl`에서 대표 레코드 1~3건만 사용해 M5 요청과 M6 번역 응답을 확인한다.

검증 조건:

- M5 응답이 유효한 JSON인가
- `is_requirement`, `is_ee_related`가 실제 boolean 또는 명확한 true/false 값인가
- E/E 레코드의 `ee_category`가 프로젝트 enum 중 하나인가
- M6가 번역문만 반환하는가
- 원문의 숫자, 단위, 식별자와 요구 강도(`shall`, `must`)가 번역에서 유지되는가
- 401, 403, 404, 429, timeout 발생 시 오류를 숨기지 않는가

API endpoint나 응답 구조가 맞지 않으면 전체 실행을 시작하지 않는다. 실제 ChatT 명세와 응답 예시를 기준으로 어댑터만 수정하고 자동 테스트를 추가한다.

### 7. 전체 파이프라인 실행

smoke test와 자동 테스트를 통과한 후 실행한다.

```powershell
python -m ee_pipeline.cli run --input "data\input" --output "output"
```

또는 단계별로 실행한다.

```powershell
python -m ee_pipeline.cli m5 --output "output_check"
python -m ee_pipeline.cli m6 --output "output_check"
python -m ee_pipeline.cli m7-m8 --output "output_check"
```

M5는 모든 후보를 다음 두 축으로 독립 판정해야 한다.

1. 요구사항인가
2. 전장품 관련인가

M6는 `is_requirement=true AND is_ee_related=true`인 항목만 번역한다.

### 8. 최종 결과 검증

M8 XLSX는 반드시 2개 시트를 가져야 한다.

#### Sheet 1: 문서 요약

- 문서별 전장품 관련 여부
- 전체 요구사항 수: `is_requirement=true` 전체
- 전장품 요구사항 수: `is_requirement=true AND is_ee_related=true`
- 전장품 요구사항 비율

#### Sheet 2: 전장 요구사항

다음 열 순서를 유지한다.

```text
doc_id / cur_id / 원문 / 한글 번역 / 비고 / 전장품 종류
```

다음을 확인한다.

- Sheet 1의 전장품 요구사항 합계와 Sheet 2 행 수가 일치하는가
- 번역이 빈 행이 없는가
- `doc_id`, `cur_id`, 원문과 출처가 보존되는가
- 같은 `block_id`가 중복되지 않는가
- API 실패 레코드가 정상 결과처럼 포함되지 않았는가

### 9. 작업 완료 보고 형식

완료 후 다음 항목을 수치로 보고한다.

```text
- 사용 문서 수와 확장자별 개수
- M2 블록 수와 빈 블록 수
- M3 청크 수와 문서 경계 오병합 수
- M4 처리 배치 수와 제외 후보 수
- M4.5 LLM 입력 수와 ID 중복 수
- M5 전체 요구사항 수
- M5 전장품 관련 요구사항 수
- M6 번역 성공/실패 수
- Sheet 1 전체/전장 요구사항 수
- Sheet 2 상세 행 수
- pytest/ruff 결과
- 생성된 XLSX 경로
- 남은 한계와 사람이 확인해야 할 항목
```

정답 라벨이 없다면 정확도 90% 이상이라고 단정하지 않는다. 대신 샘플 원문 대조 결과, 오류 유형, 누락·오탐 사례와 계산 기준을 명시한다.

### 10. Git 처리

코드를 수정했다면 다음을 지킨다.

- `.env`, `data/input/`, `output*/`, API 응답 원문을 commit하지 않는다.
- 변경 전후 테스트 결과를 보고한다.
- 회사 저장소에 직접 push 권한이 없다면 별도 브랜치에 commit하고 사용자에게 push 또는 PR 방법을 안내한다.
- 기존 정상 동작을 삭제하거나 전체 파일을 되돌리는 명령을 사용하지 않는다.

이 지침에 따라 먼저 저장소와 환경을 점검하고, 실제 ChatT 계약에서 확인이 필요한 값이 있을 때만 사용자에게 질문한 뒤 작업을 진행해라.

## 복사 끝

