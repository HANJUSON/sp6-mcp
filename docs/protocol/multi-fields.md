# SP6 Multi payload — 확인된 필드 (codec `multi-codec/0.1`)

이 문서는 `src/sp6_mcp/multi_codec.py`가 해석하는 필드와 근거를 기록한다. 근거 자료(사용자 백업, 편집기 저장 파일)는 비공개 로컬 자료이며 저장소에 포함하지 않는다. 여기에는 형식 사실과 검증 방법만 적는다.

## 컨테이너

`.ksp6`와 단일 Multi 내보내기(`.fmlt`)는 같은 레코드 문법을 쓴다.

| 레코드 | 시작 | 끝 |
|---|---|---|
| Multi | `Set**Dat*>NNNNSet*-Dat*>` | `<Set*-Dat*<Set**Dat*NNNN` |
| Program | `Prog*Dat*>NNNNProg_Dat*>` | `<Prog_Dat*<Prog*Dat*NNNN` |

- `NNNN`은 4자리 객체 ID다. 빈 슬롯은 payload 길이 0으로 기록된다.
- 레코드 밖의 바이트(Bank 머리, FX/Alg 블록 등)는 해석하지 않고 그대로 보존한다.
- 편집기 `.fmlt`(관찰 1개)는 Multi 레코드 1개 뒤에 Zone 순서대로 참조 Program 레코드가 이어지고, **각 Program 뒤에 그 Program의 FX 레코드(`FX***Data>NNNN`)와 알고리즘 레코드(`Alg**Data>NNNN`, Layer마다 반복)**가 붙는다. Multi·Program 레코드 부분은 같은 시점 Bank의 레코드와 바이트 단위로 같았고, FX/Alg 레코드도 모두 Bank 안에 같은 바이트열로 존재했다.
- Program payload(이름 20바이트부터 시작) 기준 오프셋:
  - FX 슬롯: 105, 117, 129에 `03 03 <51|52|53> <flags> <FX ID u16 BE>`. ID 0은 빈 슬롯이다.
  - Layer: `521 + 318*k` 위치마다 `03 03 51` 표지가 있고, 그 Layer의 알고리즘 ID는 표지 112바이트 앞 1바이트다(바로 앞 바이트는 플래그 값으로, 513=`02 01`처럼 보인다).
- `.fmlt` 조립 규칙(`src/sp6_mcp/closure.py`):
  - Multi 레코드
  - Zone 1→4 순서로, 켜짐/꺼짐과 관계없이 각 Zone의 Program 레코드
  - 그 뒤에 0이 아닌 FX 슬롯 순서대로 FX 레코드, 이어서 Layer 순서대로 Alg 레코드(중복 제거 없음)
  - FX/Alg 레코드는 Bank의 바이트열을 그대로 복사한다.
- 검증(2026-11-07): `SP6_user_multis_after_20261030.ksp6`의 Multi 1050으로 이 규칙을 적용하면 `만년설_1050.fmlt` 34,245바이트가 **바이트 단위로 같게** 재현된다(`tests/test_closure.py`, `SP6_MCP_PRIVATE_DIR` 지정 시 실행). Program 1은 Layer 20개, Alg 레코드 20개다. 이전 문서의 "19개"는 잘못 센 값이었다.
- 개인 백업 5개 Bank의 비어 있지 않은 Program 1,643개가 모두 이 Layer 격자를 따르고, 참조한 FX/Alg ID가 모두 Bank 안에 있었다.
- 아직 관찰하지 못한 경우는 추정하지 않고 경고(`FMLT_LAYOUT_UNVERIFIED`)를 붙인다.
  - FX 슬롯 순서가 51/52/53이 아닌 Program(백업에서 1개, 예: 51/53/52)
  - 같은 FX ID가 두 슬롯에 있는 Program
  - 여러 Zone이 같은 Program을 쓰는 Multi(현재는 한 번만 쓴다)
- 격자를 벗어난 Program이나 Bank에 없는 FX/Alg 참조는 오류로 거부한다.
- User Program(1025 이상)은 건반에 저장된 Program 위로 오래된 사본이 들어갈 가능성을 막기 위해 `.fmlt`에 넣지 않는다. 이 경우 결과는 `PARTIAL_EXPORT` 경고가 붙은 불완전 파일이다. 기본 출력은 여전히 템플릿 Bank 전체를 보존하고 빈 레코드 하나만 채우는 `.ksp6`다.
- Multi payload 안에는 자기 슬롯 ID가 없다. 서로 다른 슬롯의 payload가 바이트 단위로 같은 사례가 있어, 슬롯 ID는 레코드 머리에서만 정해진다.

로컬 검증(2026-11-06): 편집기·실기 수신으로 만든 `.ksp6` 6개와 `.fmlt` 1개가 모두 바이트 단위로 재생성됐다.

## Multi payload 머리

| 오프셋 | 길이 | 의미 | 근거 |
|---|---|---|---|
| 0 | 20 | 이름, 출력 가능 ASCII, 공백 채움 | 관찰한 164개 Multi 모두 16자 이하 + 공백. 매뉴얼 4-11 "16 characters" |
| 20 | 3 | 형식 태그 `PC3` | 관찰한 모든 Multi |

## Zone 블록

각 Multi에는 길이가 다른 Zone 블록이 정확히 4개 있다. 블록은 `03 1B <state> <channel> FF 07`로 시작하며, 아래 오프셋은 이 표지 시작점 기준이다.

| 오프셋 | 형식 | 필드 | 쓰기 | 근거 |
|---|---|---|---|---|
| +2 | `02`=켜짐, `01`=꺼짐 | `enabled` | 예 | Multi 1050 실기 수신: Zone 1·2 켜짐, 3·4 꺼짐과 일치 |
| +3 | 1–16 | `midi_channel` | 예 | 1050 Zone 3을 채널 3으로 만든 기록과 일치. 공장 Multi는 1–4 |
| +12, +13 | 7-bit 두 바이트, `hi*128+lo` | `program_id` | 예 | 1050의 Program 1/67/1과 일치. User Program 1025–1194 참조가 일관되게 해석됨 |
| +18 | 0–127 | `key_low` | 예 | 1050 분기 변경(A#3/B3→A#4/B4) 전후 비교에서 Zone 2 59→71 |
| +19 | 0–127 | `key_high` | 예 | 같은 변경에서 Zone 1 58→70 |
| +21, +22 | 부호 있는 16-bit big endian | `transpose_semitones` | 예 | 같은 기록에서 스트링 Zone 0→−12(`FF F4`). 관찰 범위 −36..+24, 이 범위만 허용 |
| `07 vv 7F 00` | 0–127 | `volume` | 아니오 | 1050 세 Zone이 설정값 115와 일치. 단일 Multi 근거라 읽기 전용 |
| `0A pp 40 00` | 0–127 | `pan` | 아니오 | 1050 세 Zone이 64(중앙)와 일치. 읽기 전용 |

검증 방법:

1. 실기에서 다시 받은 1050의 설정 기록(분기점, Program, Transpose, Zone 켜짐)과 decode 결과를 대조했다.
2. 같은 Multi의 편집 전후 스냅샷 차이(44바이트)를 위 필드 위치와 대조했다.
3. 로컬 자료의 모든 Multi(약 160개 × 6개 파일)에 대해 decode → 같은 값으로 다시 쓰기가 바이트 단위로 원본과 같은지 확인했다. 표지가 4개가 아닌 `NoName` 2개는 `UNSUPPORTED_FORMAT`으로 거부된다.

## 해석하지 않는 영역

Velocity 범위, 컨트롤러 할당·Entry, Arp 버튼, 페달, 휠, FX와 그 밖의 모든 바이트는 해석하지 않는다. 생성기는 이 영역을 템플릿 그대로 보존하고, 이 필드들을 바꾸라는 요청은 `UNSUPPORTED_FIELD`로 거부한다.

후보 관찰(쓰기 비활성, 추가 근거 필요):

- Zone 표지 기준 +31/+32가 Velocity Low/High일 가능성이 있다. +32는 모든 관찰에서 127이고, +31은 1·39·80·90·110·120 등으로 나타난다. 이 필드만 바뀐 편집기 저장 데이터가 생기기 전에는 쓰지 않는다.
- Volume·Pan의 `None` 표현은 아직 관찰하지 못했다.

## 쓰기 승격 기준

읽기 전용 필드를 쓰기 가능으로 바꾸려면, 편집기에서 **그 필드 하나만** 바꾼 전후 저장 파일이 필요하다(README AC-02). 차이가 예상한 오프셋에만 나타나는지 확인한 뒤 `FIELDS`의 `writable`과 이 표를 함께 갱신한다.

## 편집기 결과 재현 시험 (2026-11-06, 로컬)

Multi 1050의 편집 전 스냅샷에 codec으로 같은 편집(Zone 1 상한 70, Zone 2 하한 71·Transpose −12, Zone 3 Program 1·범위 71–127)을 적용했다. 결과를 편집기가 저장한 편집 후 데이터와 비교하니 **codec 필드 위치 8바이트가 모두 일치**했다. 남은 36바이트 차이는 모두 codec이 해석하지 않는 영역(편집기에서 Zone 1을 복사해 Zone 3을 만들면서 함께 바뀐 내부 값과 Volume)이었다. 즉 codec은 자기 필드만 바꾸며, Zone 복사처럼 다른 값까지 바꾸는 편집기 동작은 재현하지 않는다.
