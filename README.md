# proxy-ai

Claude Code(Anthropic API) / OpenAI 호환 클라이언트 → OpenRouter 패스스루 프록시.
`config.json` 규칙으로 모델·프로바이더·노력도를 요청에 덮어쓴다. 키는 저장하지 않고 클라이언트 헤더를 그대로 전달.

## 실행

```bash
docker compose up -d --build
```

## 설정 (`config.json`)

키 = 들어오는 model 이름(정확히 일치 우선, 다음 `*` 와일드카드를 위에서부터). 값 = 요청 body에 덮어쓸 필드.
수정 즉시 반영된다(재시작 불필요).

```json
{
  "claude-*": {
    "model": "deepseek/deepseek-v4.1-flash",
    "provider": { "only": ["baidu"], "allow_fallbacks": false },
    "effort": "high"
  },
  "*": { "provider": { "allow_fallbacks": true } }
}
```

- `model`: OpenRouter 모델 ID로 교체
- `provider`: [OpenRouter provider routing](https://openrouter.ai/docs/features/provider-routing) (`only`, `order`, `allow_fallbacks` 등)
- `effort`: `none` | `low` | `medium` | `high` — 엔드포인트에 맞게 변환된다
  (`/v1/messages` → `output_config.effort`, `none`이면 `thinking` 비활성 / `/v1/chat/completions` → `reasoning.effort`)
- 그 밖의 필드는 요청 body에 그대로 덮어쓴다

## Claude Code

```powershell
$env:ANTHROPIC_BASE_URL="http://localhost:8787"
$env:ANTHROPIC_AUTH_TOKEN=$env:OPENROUTER_API_KEY
$env:ANTHROPIC_API_KEY=""
claude
```

Claude Code가 보내는 `claude-*` 모델명은 위 규칙으로 교체되므로 모델 변수는 필요 없다.
Anthropic 외 모델로 갈 때는 Claude Code가 대화 중간에 넣는 `system` 메시지를 최상위 `system`으로 옮겨 호환시킨다.
OpenAI 호환 클라이언트는 base URL `http://localhost:8787/v1` 로 쓰면 된다.
