# proxy-ai

OpenRouter 앞단에 두는 provider 고정 패스스루 프록시. Claude Code 하네스는 그대로 두고,
`deepseek/deepseek-v4.1-flash` 요청이 **특정 제공사(Baidu Qianfan 등 할인 제공사)로만** 가도록
요청 body에 `provider` 필드를 주입한다.

- OpenRouter는 Anthropic Messages API를 직접 받으므로(`ANTHROPIC_BASE_URL=https://openrouter.ai/api`),
  OpenAI↔Anthropic 변환 없이 `provider` 필드만 끼워 넣는 패스스루면 된다.
- 프록시는 키를 저장하지 않는다. 클라이언트(Claude Code)가 보낸 `Authorization` 헤더를
  그대로 업스트림에 전달하므로, compose 파일·이미지·로그 어디에도 키가 들어가지 않는다.

검증 결과: 비스트리밍 200 + 응답 내 `"provider":"Baidu"`, 스트리밍 SSE 정상,
존재하지 않는 제공사 지정 시 404 실패(필드가 실제로 적용됨이 증명됨),
`claude -p` 파일 목록 조회 + 도구 호출 정상.

## 빠른 시작 (Docker Compose)

```bash
docker compose up -d --build
docker compose logs -f   # listening on 8787 {"only":["baidu"],...} 확인
```

이미지만 직접 쓸 때:

```bash
docker run -d --name or-proxy -p 8787:8787 \
  -e OR_PROVIDER='{"only":["baidu"],"allow_fallbacks":false}' \
  agnusdei1207/proxy-ai:latest
```

## 설정

| 변수 | 기본값 | 설명 |
|---|---|---|
| `OR_PROVIDER` | `{"only":["baidu"],"allow_fallbacks":false}` | 요청에 provider가 없고 라우트에도 없을 때의 기본 고정값 |
| `OR_ROUTES` | `{}` (없음) | 모델별 provider 테이블(JSON). 아래 "동적 선택" 참고 |
| `HOST_PORT` | `8787` | 호스트 바인딩 포트 (컨테이너 내부는 8787 고정) |
| `PORT` | `8787` | 변경 불필요 |

## 모델·프로바이더 동적 선택 (재시작 없이 바꾸는 법)

모델명은 프록시가 절대 건드리지 않는다. Claude Code의 `ANTHROPIC_MODEL` 값이
그대로 OpenRouter에 전달되므로, 모델을 바꾸려면 터미널 세션에서 변수만 바꾸면 된다.
DeepSeek에 하드코딩되어 있지 않다.

```powershell
$env:ANTHROPIC_MODEL="deepseek/deepseek-v4.1-flash"   # 다른 OpenRouter 모델 ID도 그대로 통과
```

프로바이더는 요청별로 이렇게 결정된다 (위에서부터 처음 맞는 것):

1. 요청 body에 `provider`가 이미 있으면 **그것을 존중** (직접 API 코드에서 모델마다 다르게 지정 가능).
2. `OR_ROUTES` 테이블에 요청 model과 일치하는 항목이 있으면 그것을 사용.
3. `OR_ROUTES`의 `"*"` 항목이 있으면 그것을 사용.
4. 위가 다 없으면 `OR_PROVIDER` 기본값.

`OR_ROUTES` 예시 (한 줄 JSON):

```bash
OR_ROUTES='{"deepseek/deepseek-v4.1-flash":{"only":["streamlake/fp8"],"allow_fallbacks":false},"z-ai/glm-4.6":{"only":["z-ai"],"allow_fallbacks":false},"*":{"only":["deepinfra"],"allow_fallbacks":true}}'
```

compose에서 쓰려면 `docker-compose.yml`의 environment에 추가한다:

```yaml
OR_ROUTES: '{"deepseek/deepseek-v4.1-flash":{"only":["streamlake/fp8"],"allow_fallbacks":false}}'
```

즉, Claude Code에서 `ANTHROPIC_MODEL`만 바꾸면 모델이 바뀌고,
모델별 핀은 `OR_ROUTES` 한 줄로 관리된다. 컨테이너를 모델마다 띄울 필요가 없다.

제공사를 바꾸려면 `docker-compose.yml`의 `OR_PROVIDER` 한 줄만 교체 후 `docker compose up -d`:

```yaml
# StreamLake (약 51% 할인) 예시
OR_PROVIDER: '{"only":["streamlake/fp8"],"allow_fallbacks":false}'
```

`.env` 파일을 쓸 수도 있다 (`.env.example` 복사, `.env`는 git에 커밋되지 않음).

## Claude Code 설정법

프록시가 뜬 상태에서 **새 터미널**에 아래를 설정한다. 셸 프로파일·`~/.claude`는 건드리지 않는다.

PowerShell (최소 4개):

```powershell
$env:ANTHROPIC_BASE_URL="http://localhost:8787"
$env:ANTHROPIC_AUTH_TOKEN=$env:OPENROUTER_API_KEY
$env:ANTHROPIC_API_KEY=""
$env:ANTHROPIC_MODEL="deepseek/deepseek-v4.1-flash"
claude -p "현재 디렉터리의 파일 목록을 보여줘"
```

bash (최소 4개):

```bash
export ANTHROPIC_BASE_URL="http://localhost:8787"
export ANTHROPIC_AUTH_TOKEN="$OPENROUTER_API_KEY"
export ANTHROPIC_API_KEY=""
export ANTHROPIC_MODEL="deepseek/deepseek-v4.1-flash"
claude -p "현재 디렉터리의 파일 목록을 보여줘"
```

주의:

- 기존 셸에 다른 `ANTHROPIC_MODEL`이 남아 있으면 그것이 그대로 전달되므로
  위처럼 세션에서 함께 지정해야 한다.
- 기존 Anthropic 로그인이 캐시되어 충돌하면 Claude Code에서 `/logout` 후 재시작
  (판단되면 직접 하지 말고 확인 후 진행).
- 타사 모델에서는 Claude Code의 일부 도구 호출 호환성이 떨어질 수 있다
  (OpenRouter 공식 문서도 Anthropic 1P 외 동작을 보장하지 않음).

## 검증 (키 없이 확인하는 법은 아래 보안 참고)

```bash
# (a) 비스트리밍
curl -s http://localhost:8787/v1/messages \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" -H "content-type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"deepseek/deepseek-v4.1-flash","max_tokens":50,"messages":[{"role":"user","content":"hi"}]}'
# 응답 본문의 "provider":"Baidu" 확인

# (b) 스트리밍: 위 body에 "stream":true 추가 후 SSE 수신 확인

# (d) 부정 대조군: OR_PROVIDER를 존재하지 않는 제공사로 바꿔 띄운 뒤 (a) 재실행 → 실패해야 정상
# ("No allowed providers ... permits only: ..." 404)
```

## 보안

- OpenRouter 키는 절대 이 리포에 커밋하지 않는다 (`.env`는 `.gitignore` 처리됨).
- 키는 환경변수(`OPENROUTER_API_KEY`)로만 사용하고, 파일·로그·git·셸 히스토리에 남기지 않는다.
- 키가 한 번이라도 채팅·로그 등에 노출됐다면
  [OpenRouter 키 설정](https://openrouter.ai/settings/keys)에서 revoke 후 재발급한다.

## 구조

```text
proxy-ai/
├── or-proxy.py         # 패스스루 프록시 (Python 표준라이브러리만, 의존성 없음)
├── Dockerfile           # python:3-alpine 기반
├── docker-compose.yml   # OR_PROVIDER / HOST_PORT 설정
├── .env.example         # 로컬 설정 템플릿 (복사해서 .env 로 사용)
└── README.md
```
