# 사용법 (v0.8.0)

> 비공식 도구다. 이 서버는 MIDI 포트를 열지 않고 건반에 아무것도 보내지 않는다. 생성 파일을 건반에 저장하는 일은 사용자가 SP6 Sound Editor로 직접 한다.

## 설치

```powershell
cd C:\path\to\sp6-mcp
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest        # 합성 데이터 테스트
```

Python 3.11 이상, MCP Python SDK 2.3 이상(`mcp>=2.3,<3`)이 필요하다.

## 경로 규칙

| 환경 변수 | 기본값 | 의미 |
|---|---|---|
| `SP6_MCP_WORK_ROOT` | 현재 폴더 | 템플릿·백업 같은 **입력** 상대 경로의 기준 |
| `SP6_MCP_ARTIFACTS_DIR` | `<작업 루트>/artifacts` | 생성 파일을 쓸 수 있는 **유일한** 폴더 |

- 입력은 상대 경로(작업 루트 기준) 또는 절대 경로로 준다. 서버는 입력 파일을 수정하지 않는다.
- 출력 `output_path`는 artifacts 폴더 기준 상대 경로만 받는다. `..`, 절대 경로, 폴더 밖으로 나가는 링크, 이미 있는 파일은 `OUTPUT_PATH_DENIED`.

## MCP 클라이언트 등록

Claude Code (사용자 범위):

```powershell
claude mcp add sp6 --scope user `
  -e SP6_MCP_WORK_ROOT="C:\손한주\05Music" `
  -e SP6_MCP_ARTIFACTS_DIR="C:\손한주\05Music\sp6-mcp\artifacts" `
  -- "C:\손한주\05Music\sp6-mcp\.venv\Scripts\python.exe" -m sp6_mcp.server
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "sp6": {
      "command": "C:\\손한주\\05Music\\sp6-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "sp6_mcp.server"],
      "env": {
        "SP6_MCP_WORK_ROOT": "C:\\손한주\\05Music",
        "SP6_MCP_ARTIFACTS_DIR": "C:\\손한주\\05Music\\sp6-mcp\\artifacts"
      }
    }
  }
}
```

## 도구

| 도구 | 하는 일 | 부작용 |
|---|---|---|
| `sp6_capabilities` | 지원 형식, 쓸 수 있는 필드와 근거, 장치 기능 미구현 표시 | 없음 |
| `sp6_spec_schema` | 설정표 JSON Schema | 없음 |
| `sp6_file_inspect` | 파일 해시, 인벤토리, 객체 목록(페이지) | 없음 |
| `sp6_object_find` | 이름으로 Program/Multi 번호 찾기 (예: "Prophet Pad" → 87) | 없음 |
| `sp6_multi_read` | Multi의 Zone별 켜짐·Channel·Program(이름)·범위·Transpose·Volume·Pan | 없음 |
| `sp6_multi_validate` | 설정표 검증: 오류·경고와 필드 경로, 실제 소리 나는 음역 | 없음 |
| `sp6_multi_build` | artifacts 폴더에 새 `.ksp6`(기본: 템플릿 Bank 사본 + 빈 레코드 하나에 새 Multi) 또는 편집기 배치를 따른 `.fmlt`(Program마다 FX·Alg 레코드 포함, User Program 제외)와 `.manifest.json` 생성. 확장자는 형식과 같아야 한다 | 새 로컬 파일 |
| `sp6_artifact_diff` | 템플릿과 생성 파일의 레코드·필드 차이 | 없음 |

성공 결과는 `status`, `operation_id`, `phase`, `warnings`, `evidence`, `result`를 가진다. 실패는 MCP 도구 오류이며 본문은 `{"status":"error","error":{code,message,field,retryable,device_write_state}}` JSON이다.

## 일반 흐름

1. `sp6_file_inspect`로 백업 파일의 `sha256`과 User Multi 사용 현황을 본다.
2. `sp6_object_find`로 원하는 음색 번호를 찾는다.
3. 템플릿 Multi를 고르고 `sp6_multi_read`로 Zone 상태를 확인한다. 공장 Multi를 템플릿으로 쓰면 기존 사용자 Multi를 건드릴 일이 없다.
4. 설정표를 만든다([examples/standup.sheet.example.json](../examples/standup.sheet.example.json)). 바꾸지 않을 필드는 **생략**한다. 생략한 필드는 템플릿 값을 유지한다.
5. `sp6_multi_validate` → `can_build: true`인지 본다. `unsupported_fields`에 나온 필드는 빼야 한다.
6. `sp6_multi_build` → `.ksp6` 생성(`preferred_user_multi_id` 레코드가 템플릿 파일에서 비어 있어야 한다). 결과의 `effective_zones`, `changes`, `not_adjusted`를 사용자에게 쉬운 말로 설명한다.

## 건반에 넣기 (수동, 아직 자동화하지 않음)

1. SP6 Sound Editor에서 건반에 연결하고 **MIDI → Receive Data → Multi**로 User Multi를 먼저 받는다. 받기 전에는 Write 창이 모든 슬롯을 비어 있는 것처럼 보여 위험하다. 받은 데이터는 새 `.ksp6`로 백업한다.
2. 생성한 `.ksp6`를 열어 해당 번호의 Multi를 고르고 Zone·Program·범위가 결과와 같은지 확인한다(이 확인이 11장의 `파일 해석 확인됨` 단계다).
3. 받은 목록에서 **비어 있는 것이 확인된** 슬롯에 그 Multi **한 건만** 저장한다. Send All·Bank 전송은 절대 쓰지 않는다. 파일의 다른 Program·Multi는 템플릿 백업과 같은 데이터이므로 보내지 않는다.
4. 저장 후 그 슬롯을 다시 받아 같은지 확인한다. 편집 중에는 건반 패널을 만지지 않는다(편집 버퍼가 다시 동기화되며 수정이 사라질 수 있다).

## 한계 (codec `multi-codec/0.1`)

- 쓰기: 이름, Zone 켜짐, MIDI Channel, Program, Key Low/High, Transpose.
- 읽기만: Volume, Pan.
- 해석하지 않음(템플릿 값 유지): Velocity, 컨트롤러(ModWheel·FILTER1·Entry), Arp 버튼, 페달, 휠, FX.
- 근거와 승격 기준: [protocol/multi-fields.md](protocol/multi-fields.md).

## 비공개 자료 검증

```powershell
.venv\Scripts\python scripts\verify_local_backups.py "C:\path\backup.ksp6" > private\verify.json
```

모든 레코드의 round-trip과 Multi codec 자기 일관성을 확인한다. 출력에는 개인 정보가 들어갈 수 있으므로 `private/`(git 제외)에 둔다.
